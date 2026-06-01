"""Value-factor confirmation — is VALUE robust enough on this universe to tilt the funnel?

The held-out gate (compounder_heldout_oos.py) found gross_quality did NOT generalize out-of-time
but VALUE (qarp, ≈ cheap ps/pb) did (+0.191, 3/3, +31% long-only, size-robust). Value is one of
the most-replicated factors in finance, so this is a CONFIRMATION + power check, not a discovery:
a PRE-REGISTERED, untuned value composite tested over the FULL 2012-2022 as-of span (11 windows
at 3y, far more power than the 3-window held-out) on PINNED prices, with the same controls the
audit demanded (sector-neutral + size-partial IC, long-only top-decile-vs-equal-weight, regime
thirds, turnover-cost haircut). The question this answers: should a value tilt be added to the
funnel, and is it robust to size/sector/cost — gating any `_WEIGHTS` change.

PRE-REGISTERED (LOCKED, untuned): value = mean oriented rank of (cheap ps, cheap pb).

Output: out/compounder-value-validation.md
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.fundamentals_snapshot import read_fundamentals_snapshot  # noqa: E402
from data.models import FundamentalRecord  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.compounder import SECTOR_INVALID_METRICS, rank_compounders  # noqa: E402

# Reuse the held-out script's vetted, tested helpers (DRY; covered by test_compounder_heldout_oos).
from scripts.compounder_heldout_oos import (  # noqa: E402
    composite_values,
    partial_spearman,
    price_asof,
    sector_neutral_ic,
    spearman,
)

DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"
DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-2026-06-01.csv"
DEFAULT_OUT = ROOT / "out" / "compounder-value-validation.md"

HORIZON = 3
# Candidate as-of dates; the actual cutoff (which as-of's have a 3y-forward price) is derived
# from the pinned price snapshot's last date at run time, so a newer --prices adds windows.
AS_OF = [date(y, 6, 30) for y in range(2012, 2025)]
MIN_PAIRS = 40
DECILE = 10
ONE_WAY_BPS = 30.0

# PRE-REGISTERED, untuned. value = cheap ps + cheap pb (standard value factor).
COMPOSITES: dict[str, list[tuple[str, int]]] = {
    "value": [("ps", -1), ("pb", -1)],
    "value_pfcf": [("ps", -1), ("pb", -1), ("pfcf", -1)],  # adds cash-flow yield (pre-declared)
}


def load_symbols(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as f:
        return sorted({r["symbol"].upper() for r in csv.DictReader(f)})


def load_sectors(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["symbol"].upper()] = r.get("sector") or "unknown"
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def main() -> None:
    args = parse_args()
    for pth, what in ((args.snapshot, "snapshot"), (args.prices, "pinned prices")):
        if not pth.exists():
            raise SystemExit(f"{what} not found: {pth}")
    symbols = load_symbols(args.universe)
    sectors = load_sectors(args.sectors)
    funds: dict[str, list[FundamentalRecord]] = {}
    for rec in read_fundamentals_snapshot(args.snapshot, verify=True):
        funds.setdefault(rec.symbol.upper(), []).append(rec)
    for v in funds.values():
        v.sort(key=lambda r: r.asof_ts)
    print(f"Loading PINNED prices {args.prices.name}...")
    closes = read_price_snapshot(args.prices, verify=True)
    # Derive the forward-data cutoff from the pinned snapshot (not a hardcoded date), so a
    # newer --prices automatically unlocks later as-of windows.
    data_end = closes.index.max().date()

    # rows[comp] = list of per-as-of dicts
    rows: list[dict] = []
    top_members: dict[str, dict[date, list[str]]] = {c: {} for c in COMPOSITES}
    as_of_used: list[date] = []
    for as_of in AS_OF:
        fwd_date = date(as_of.year + HORIZON, as_of.month, as_of.day)
        if fwd_date > data_end:
            continue
        universe: dict[str, tuple[Sequence[FundamentalRecord], float]] = {}
        as_of_price: dict[str, float] = {}
        for sym in symbols:
            if sym not in closes.columns:
                continue
            recs = [r for r in funds.get(sym, []) if r.asof_ts.date() <= as_of]
            if len(recs) < 2:
                continue
            p0 = price_asof(closes[sym], as_of)
            if p0 is None or p0 <= 0:
                continue
            universe[sym] = (recs, p0)
            as_of_price[sym] = p0
        ranked = rank_compounders(universe, top_n=10_000, sectors=sectors)
        if len(ranked) < MIN_PAIRS:
            continue
        metrics_by_sym: dict[str, dict[str, float | None]] = {}
        mcap: dict[str, float] = {}
        for c in ranked:
            invalid = SECTOR_INVALID_METRICS.get(sectors.get(c.symbol, "unknown"), frozenset())
            metrics_by_sym[c.symbol] = {
                k: (None if k in invalid else v) for k, v in c.metrics.items()
            }
            mc = c.metrics.get("market_cap")
            if mc is not None:
                mcap[c.symbol] = mc
        fwd: dict[str, float] = {}
        for sym in as_of_price:
            p1 = price_asof(closes[sym], fwd_date)
            if p1 is not None:
                fwd[sym] = p1 / as_of_price[sym] - 1.0
        if len(fwd) < MIN_PAIRS:
            continue
        univ_mean = statistics.mean(fwd.values())
        for comp, comps in COMPOSITES.items():
            vals = composite_values(metrics_by_sym, comps)
            pairs = [(s, vals[s], fwd[s]) for s in vals if s in fwd]
            if len(pairs) < MIN_PAIRS:
                continue
            ic = spearman([p[1] for p in pairs], [p[2] for p in pairs])
            sn = sector_neutral_ic(vals, fwd, sectors)
            psize = [(vals[s], fwd[s], mcap[s]) for s in vals if s in fwd and s in mcap]
            part = (
                partial_spearman(
                    [p[0] for p in psize], [p[1] for p in psize], [p[2] for p in psize]
                )
                if len(psize) >= MIN_PAIRS
                else None
            )
            ordered = sorted((s for s in vals if s in fwd), key=lambda s: vals[s], reverse=True)
            k = max(1, len(ordered) // DECILE)
            top = ordered[:k]
            top_members[comp][as_of] = top
            top_excess = statistics.mean(fwd[s] for s in top) - univ_mean
            rows.append(
                {
                    "as_of": as_of,
                    "comp": comp,
                    "ic": ic,
                    "sn": sn,
                    "part": part,
                    "top_excess_ann": (1 + top_excess) ** (1 / HORIZON) - 1
                    if top_excess > -1
                    else None,
                }
            )
        as_of_used.append(as_of)
        print(f"  {as_of} -> {fwd_date} done")

    if not rows:
        raise SystemExit("no windows produced — check --prices / --snapshot.")
    n_win = len(as_of_used)

    def sub(comp: str, key: str, pred=lambda r: True) -> list[float]:
        return [r[key] for r in rows if r["comp"] == comp and pred(r) and r[key] is not None]

    def mean_of(xs: list[float]) -> float | None:
        return statistics.mean(xs) if xs else None

    def annual_turnover(comp: str) -> float | None:
        asofs = sorted(top_members[comp])
        if len(asofs) < 2:
            return None
        drops = []
        for a, b in zip(asofs, asofs[1:], strict=False):
            prev, cur = set(top_members[comp][a]), set(top_members[comp][b])
            if prev:
                drops.append((len(prev - cur) / len(prev)) / ((b - a).days / 365.25))
        return statistics.mean(drops) if drops else None

    early = lambda r: r["as_of"].year <= 2015  # noqa: E731
    mid = lambda r: 2016 <= r["as_of"].year <= 2019  # noqa: E731
    late = lambda r: r["as_of"].year >= 2020  # noqa: E731

    md = [
        "# Compounder — Value-Factor Confirmation (full-period, pinned prices)",
        "",
        f"Research-only. PRE-REGISTERED untuned composites: `value` = cheap ps + pb; "
        f"`value_pfcf` = + cheap pfcf. Full as-of span {as_of_used[0]}..{as_of_used[-1]} "
        f"(3y fwd, **{n_win} windows** — {n_win / 3:.1f}× the 3-window held-out power), PINNED "
        f"prices (cutoff {data_end}), sector-nulled to match the live scorer. Value is a standard "
        f"replicated factor, so this is a CONFIRMATION + power/robustness check, not a discovery.",
        "",
        "## Pooled IC + controls (per composite, over all windows)",
        "",
        "| Composite | raw IC | pos | sector-neutral | size-partial | long-only top-decile excess (ann) |",
        "|---|--:|--:|--:|--:|--:|",
    ]
    for comp in COMPOSITES:
        ics = sub(comp, "ic")
        md.append(
            f"| {comp} | {_fmt(mean_of(ics))} | {sum(1 for x in ics if x > 0)}/{len(ics)} | "
            f"{_fmt(mean_of(sub(comp, 'sn')))} | {_fmt(mean_of(sub(comp, 'part')))} | "
            f"{_pct(mean_of(sub(comp, 'top_excess_ann')))} |"
        )

    md += [
        "",
        "## Regime stability (raw IC by sub-period) + cost",
        "",
        "| Composite | 2012-15 | 2016-19 | 2020-23 | ann. turnover | net top-decile (cost-adj) |",
        "|---|--:|--:|--:|--:|--:|",
    ]
    for comp in COMPOSITES:
        turn = annual_turnover(comp)
        gross = mean_of(sub(comp, "top_excess_ann"))
        net = (
            gross - turn * 2 * (ONE_WAY_BPS / 1e4)
            if (gross is not None and turn is not None)
            else None
        )
        md.append(
            f"| {comp} | {_fmt(mean_of(sub(comp, 'ic', early)))} | "
            f"{_fmt(mean_of(sub(comp, 'ic', mid)))} | {_fmt(mean_of(sub(comp, 'ic', late)))} | "
            f"{_pct(turn)} | {_pct(net)} |"
        )

    # verdict
    v_ic = mean_of(sub("value", "ic"))
    v_pos = sub("value", "ic")
    v_sn = mean_of(sub("value", "sn"))
    v_part = mean_of(sub("value", "part"))
    v_regimes = [mean_of(sub("value", "ic", p)) for p in (early, mid, late)]
    regime_ok = all(x is not None and x > 0 for x in v_regimes)
    size_robust = v_ic is not None and v_part is not None and v_part > 0.5 * v_ic and v_part > 0
    broad = v_ic is not None and v_ic > 0 and sum(1 for x in v_pos if x > 0) >= 0.7 * len(v_pos)
    # The verdict claims sector-neutral + cost survival, so gate on them too (not just regime/
    # size/breadth): sector-neutral IC must be positive AND the long-only top-decile must stay
    # positive after the turnover-cost haircut.
    sn_ok = v_sn is not None and v_sn > 0
    v_turn = annual_turnover("value")
    v_gross = mean_of(sub("value", "top_excess_ann"))
    v_net = (
        v_gross - v_turn * 2 * (ONE_WAY_BPS / 1e4)
        if (v_gross is not None and v_turn is not None)
        else None
    )
    cost_ok = v_net is not None and v_net > 0

    md += [
        "",
        "## Verdict",
        "",
        f"- value pooled IC {_fmt(v_ic)} ({sum(1 for x in v_pos if x > 0)}/{len(v_pos)} windows +); "
        f"sector-neutral {_fmt(v_sn)}; size-partial {_fmt(v_part)}.",
        f"- regime ICs: 2012-15 {_fmt(v_regimes[0])}, 2016-19 {_fmt(v_regimes[1])}, "
        f"2020-23 {_fmt(v_regimes[2])}.",
        "",
        (
            "**VALUE IS ROBUST** on this universe — positive pooled IC, positive in all three "
            "regimes, survives sector/size controls, and the long-only top-decile beats the "
            f"equal-weight universe after costs. This is a defensible, well-powered ({n_win} "
            "windows) basis for a VALUE TILT in the funnel (e.g. up-weight `value_turnaround` "
            "or add a cheap-ps/pb signal to the quality archetype). Still gate the actual "
            "`_WEIGHTS` change behind a final spec + Codex review; do not over-fit the weights."
            if regime_ok and broad and size_robust and sn_ok and cost_ok
            else "**VALUE IS NOT UNIFORMLY ROBUST HERE** — it fails at least one control "
            "(regime / breadth / size / sector-neutral / cost). See the tables; do NOT tilt "
            "`_WEIGHTS` to value yet."
        ),
        "",
        "Caveats: survivorship (current constituents — biases value LESS than quality, but "
        f"delisted cheap names are absent); {n_win} overlapping 3y windows (better power than the "
        "held-out 3, but still autocorrelated — eff N ~4-5); long-only top-decile vs equal-weight "
        "is a coarse proxy for a real backtest; turnover cost is a haircut, not modeled impact; "
        "value is famously regime-cyclical (long value winters), so 'robust here' ≠ 'always'.",
    ]
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n" + "\n".join(md[md.index("## Verdict") :]))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
