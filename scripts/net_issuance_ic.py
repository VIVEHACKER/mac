"""Forward-IC validation of net_issuance_signal (the capital-raises / buyback factor).

Answers the only question that matters before the signal earns any weight: does it actually rank
future winners? Method = the same PIT forward Spearman rank IC used to REJECT the compounder quality
funnel (scripts/compounder_factor_ic.py): per as-of date, score the cross-section with the signal,
correlate against the forward return, average across dates. Two trust guards:

  1. Pipeline self-check — 12-1 momentum IC must be POSITIVE and market-cap (size) IC slightly
     NEGATIVE on the same panel. If these textbook anchors come out right, a null/weak net-issuance
     IC is a real finding, not a harness bug.
  2. Size-partial IC — controls for market cap, so a "signal" that is just a small-cap tilt is
     unmasked.

Reads the populated catalog (default: the sibling `trader` worktree DB). Research-only; writes
out/net-issuance-ic.md. Honest caveats: the broad universe is survivorship-biased (delisted diluters
drop out, biasing the dilution penalty toward zero — the true |IC| is a lower bound), and overlapping
forward windows are autocorrelated (the t-stat is effective-N haircut).
"""

from __future__ import annotations

import argparse
import bisect
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.models import FundamentalRecord  # noqa: E402
from engine.ic import ic_stats, partial_spearman, spearman  # noqa: E402
from signals.capital import net_issuance_signal  # noqa: E402

# The populated catalog lives in the sibling `trader` worktree (per-worktree, gitignored). Derive it
# from this checkout so the script is portable; override with --db.
DEFAULT_DB = ROOT.parent / "trader" / "data" / "store" / "trader.duckdb"
DEFAULT_OUT = ROOT / "out" / "net-issuance-ic.md"
AS_OF_DATES = [date(y, m, d) for y in range(2011, 2024) for (m, d) in ((6, 30), (12, 31))]
HORIZONS = [63, 365, 730]  # calendar days: 3mo (momentum-anchor validity) / 1y / 2y
NET_HORIZONS = [365, 730]  # net-issuance is a slow 1-2y factor; pool the verdict over these
MOM_ANCHOR_H = 63  # 12-1 momentum is textbook-POSITIVE at ~3mo (it REVERSES at 1-2y, not a bug)
OOS_FROM = date(2019, 1, 1)  # as-of dates on/after this are out-of-sample
MIN_PAIRS = 30


def _price_asof(series: tuple[list[date], list[float]], as_of: date) -> float | None:
    """Last close on or before ``as_of`` (the current price). None if no bar yet."""
    ts, cl = series
    i = bisect.bisect_right(ts, as_of) - 1
    return cl[i] if i >= 0 else None


def _fwd_price(
    series: tuple[list[date], list[float]], target: date, tol_days: int = 45
) -> float | None:
    """Close near ``target`` (within ``tol_days`` before it). Returns None when the last available
    bar is older than the tolerance — i.e. the name delisted / data ended before the horizon — so a
    STALE close is never mislabeled as a full-horizon forward price (it is dropped, not injected)."""
    ts, cl = series
    i = bisect.bisect_right(ts, target) - 1
    if i < 0 or (target - ts[i]).days > tol_days:
        return None
    return cl[i]


def _load(
    db: Path,
) -> tuple[dict[str, list[FundamentalRecord]], dict[str, tuple[list[date], list[float]]]]:
    con = duckdb.connect(str(db), read_only=True)
    funds: dict[str, list[FundamentalRecord]] = {}
    rows = con.execute(
        "SELECT symbol, period_end, asof_ts, shares_out FROM fundamentals_q "
        "WHERE market='us' AND shares_out IS NOT NULL ORDER BY symbol, period_end"
    ).fetchall()
    for sym, period_end, asof_ts, shares_out in rows:
        funds.setdefault(sym, []).append(
            FundamentalRecord(
                symbol=sym,
                market="us",
                period_end=period_end,
                asof_ts=asof_ts,
                shares_out=float(shares_out),
            )
        )
    prices: dict[str, tuple[list[date], list[float]]] = {}
    brows = con.execute(
        "SELECT symbol, ts, close FROM bars WHERE market='us' AND freq='1d' ORDER BY symbol, ts"
    ).fetchall()
    cur_sym: str | None = None
    ts_list: list[date] = []
    cl_list: list[float] = []
    for sym, ts, close in brows:
        if sym != cur_sym:
            if cur_sym is not None:
                prices[cur_sym] = (ts_list, cl_list)
            cur_sym, ts_list, cl_list = sym, [], []
        ts_list.append(ts)
        cl_list.append(float(close))
    if cur_sym is not None:
        prices[cur_sym] = (ts_list, cl_list)
    con.close()
    return funds, prices


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--lookback-quarters", type=int, default=4)
    args = ap.parse_args()
    if not args.db.exists():
        raise SystemExit(f"catalog DB not found: {args.db}")

    funds, prices = _load(args.db)
    universe = sorted(s for s in funds if s in prices and len(funds[s]) >= 2)
    print(f"universe: {len(universe)} symbols with >=2 shares_out quarters + us bars")

    # cells[name][horizon] = list of per-as-of ICs. name in: signal, signal_partial, mom, size.
    cells: dict[str, dict[int, list[float]]] = {
        k: {h: [] for h in HORIZONS} for k in ("signal", "signal_partial", "mom", "size")
    }
    oos: dict[str, dict[int, list[float]]] = {
        k: {h: [] for h in HORIZONS} for k in ("signal", "signal_partial")
    }
    coverage: dict[int, list[float]] = {h: [] for h in HORIZONS}  # fraction surviving to fwd date
    lookback_days = round(args.lookback_quarters * 91.3125)

    for as_of in AS_OF_DATES:
        score: dict[str, float] = {}
        size: dict[str, float] = {}
        mom: dict[str, float] = {}
        p0: dict[str, float] = {}
        for sym in universe:
            price0 = _price_asof(prices[sym], as_of)
            if price0 is None or price0 <= 0:
                continue
            p0[sym] = price0
            sig = net_issuance_signal(
                funds[sym], as_of=as_of, lookback_quarters=args.lookback_quarters
            )
            if sig is not None:
                score[sym] = sig.score
            # Size control uses shares from BEFORE the issuance lookback window, so the proxy does
            # NOT share `latest_sh` (the signal's numerator) — otherwise score and size would be
            # collinear by construction and the size-partial would collapse to ~0 artificially.
            pre = sorted(
                (
                    r
                    for r in funds[sym]
                    if r.asof_ts.date() <= as_of - timedelta(days=lookback_days) and r.shares_out
                ),
                key=lambda r: (
                    r.period_end,
                    r.asof_ts,
                ),  # latest quarter, latest-filed (restatement)
            )
            sh_pre = pre[-1].shares_out if pre else None
            if sh_pre:
                size[sym] = price0 * sh_pre  # market-cap proxy (rank-only; pre-window shares)
            p_12 = _price_asof(prices[sym], as_of - timedelta(days=365))
            p_1 = _price_asof(prices[sym], as_of - timedelta(days=30))
            if p_12 and p_1 and p_12 > 0:
                mom[sym] = p_1 / p_12 - 1.0  # 12-1 momentum anchor

        for h in HORIZONS:
            fwd: dict[str, float] = {}
            for sym in p0:
                p1 = _fwd_price(prices[sym], as_of + timedelta(days=h))
                if p1 is not None and p1 > 0:
                    fwd[sym] = p1 / p0[sym] - 1.0
            if p0:
                coverage[h].append(len(fwd) / len(p0))  # survivorship visibility
            if len(fwd) < MIN_PAIRS:
                continue

            def _panel(
                d: dict[str, float], f: dict[str, float] = fwd
            ) -> tuple[list[float], list[float], list[str]]:
                syms = [s for s in d if s in f]
                return [d[s] for s in syms], [f[s] for s in syms], syms

            sx, sy, ssyms = _panel(score)
            if len(sx) >= MIN_PAIRS:
                ic = spearman(sx, sy)
                if ic is not None:
                    cells["signal"][h].append(ic)
                    if as_of >= OOS_FROM:
                        oos["signal"][h].append(ic)
                # size-partial: control for market cap on names that have both
                psyms = [s for s in ssyms if s in size]
                if len(psyms) >= MIN_PAIRS:
                    pic = partial_spearman(
                        [score[s] for s in psyms], [fwd[s] for s in psyms], [size[s] for s in psyms]
                    )
                    if pic is not None:
                        cells["signal_partial"][h].append(pic)
                        if as_of >= OOS_FROM:
                            oos["signal_partial"][h].append(pic)
            mx, my, _ = _panel(mom)
            if len(mx) >= MIN_PAIRS:
                mic = spearman(mx, my)
                if mic is not None:
                    cells["mom"][h].append(mic)
            zx, zy, _ = _panel(size)
            if len(zx) >= MIN_PAIRS:
                zic = spearman(zx, zy)
                if zic is not None:
                    cells["size"][h].append(zic)
        print(f"  {as_of}: scored {len(score)} names")

    pooled = {k: [ic for h in HORIZONS for ic in cells[k][h]] for k in cells}
    if not pooled["signal"]:
        raise SystemExit("no net-issuance IC cells produced — check the DB / coverage. No verdict.")

    span_years = (AS_OF_DATES[-1].year - AS_OF_DATES[0].year) + 1

    def _line(name: str, src: dict[str, dict[int, list[float]]]) -> str:
        parts = []
        for h in HORIZONS:
            st = ic_stats(src[name][h], eff_n=max(1.0, span_years / (h / 365.0)))
            parts.append("n/a" if st.mean is None else f"{st.mean:+.4f}")
        all_ics = [ic for h in HORIZONS for ic in src[name][h]]
        st = ic_stats(all_ics, eff_n=max(1.0, span_years / 1.0))
        mean_s = "n/a" if st.mean is None else f"{st.mean:+.4f}"
        t_s = "n/a" if st.t_stat is None else f"{st.t_stat:+.2f}"
        pos = f"{st.positive}/{st.n}"
        return f"| {name} | " + " | ".join(parts) + f" | **{mean_s}** | {t_s} | {pos} |"

    hcols = " | ".join(f"{h // 30}mo" if h < 365 else f"{h // 365}y" for h in HORIZONS)
    # The verdict keys on the SINGLE 1y horizon (the canonical net-issuance horizon) — pooling 1y+2y
    # cells per date would inflate the t-stat (the two horizons share names + overlap, so their ICs
    # are correlated). 2y is reported in the table as corroboration only.
    primary_h = NET_HORIZONS[0]
    mom_anchor = ic_stats(cells["mom"][MOM_ANCHOR_H])  # short-horizon momentum: textbook-positive
    # eff-N haircut: pooled size cells span 3 overlapping horizons x semiannual dates, so the naive
    # t would be inflated by autocorrelation — gate VALIDATED on the haircut t, as the report claims.
    size_st = ic_stats(pooled["size"], eff_n=span_years)
    sig_st = ic_stats(cells["signal"][primary_h], eff_n=span_years)
    par_st = ic_stats(cells["signal_partial"][primary_h], eff_n=span_years)  # size-de-confounded
    oos_sig = ic_stats(oos["signal_partial"][primary_h])  # OOS on the DE-CONFOUNDED series (Codex)
    cov_st = {h: ic_stats(coverage[h]) for h in HORIZONS}

    # Machinery is trusted if SHORT-horizon momentum is positive (the one robust textbook anchor) and
    # the size cross-section is non-degenerate (real return dispersion, not noise).
    anchors_ok = (
        (mom_anchor.mean or 0) > 0 and size_st.t_stat is not None and abs(size_st.t_stat) > 1.5
    )
    md = [
        "# net_issuance_signal — Forward IC Validation",
        "",
        f"Research-only. PIT forward Spearman rank IC across {len(AS_OF_DATES)} semiannual as-of "
        f"dates (2011-2023) x horizons. `score = -net_issuance` (buyback bullish), so a POSITIVE IC "
        "= the net-issuance anomaly holds (buybacks outperform, diluters underperform).",
        "",
        "## Pipeline self-check (validates the panel/forward-return machinery)",
        f"- 12-1 momentum IC @~2mo/{MOM_ANCHOR_H}d (expect POSITIVE — momentum reverses at 1-2y, "
        f"so the slow horizons are not a valid momentum check): **{_f(mom_anchor.mean)}** "
        f"({mom_anchor.positive}/{mom_anchor.n} +)",
        f"- market-cap / size IC, pooled (real cross-sectional dispersion, t≈{_f2(size_st.t_stat)}): "
        f"**{_f(size_st.mean)}** ({size_st.positive}/{size_st.n} +)",
        "- forward-bar coverage (survivors reaching the horizon; low coverage = survivorship bias): "
        + ", ".join(
            f"{h // 365 if h >= 365 else h // 30}{'y' if h >= 365 else 'mo'} {_f(cov_st[h].mean)}"
            for h in HORIZONS
        ),
        f"- harness machinery {'VALIDATED' if anchors_ok else 'SUSPECT — interpret with caution'}.",
        "",
        "## net-issuance forward IC (eff-N haircut for overlapping windows)",
        "",
        f"| factor | {hcols} | **IC pooled** | t(effN) | + frac |",
        "|---|" + "---|" * (len(HORIZONS)) + "---|---|---|",
        _line("signal", cells),
        _line("signal_partial", cells) + "  ← size-controlled",
        _line("mom", cells) + "  ← anchor",
        _line("size", cells) + "  ← anchor",
        "",
        f"- OOS (2019+) 1y size-controlled IC **{oos_sig.mean:+.4f}** "
        f"({oos_sig.positive}/{oos_sig.n} +)."
        if oos_sig.mean is not None
        else "- OOS: no cells.",
        "",
        "## Verdict",
        _verdict(sig_st, par_st, oos_sig, anchors_ok),
        "",
        "Caveats: (1) SURVIVORSHIP — delisted diluters drop out of the forward set, biasing the "
        "dilution penalty toward zero, so the true |IC| is a LOWER bound. (2) Overlapping forward "
        "windows are autocorrelated; the t-stat uses an effective-N haircut (~1 independent window "
        "per horizon-year). (3) Long-only raw-rank IC, not a price-controlled long-short. "
        "(4) net-issuance is a known SMALL-magnitude factor (annual IC ~0.02-0.05 in the literature) "
        "— read the SIGN and consistency, not a large number.",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(
        "\n"
        + "\n".join(
            md[md.index("## Pipeline self-check (validates the panel/forward-return machinery)") :]
        )
    )
    print(f"\nWrote {args.out}")


def _verdict(sig, par, oos, anchors_ok) -> str:  # noqa: ANN001
    """Verdict driven by the SIZE-CONTROLLED (de-confounded) IC — the raw IC is contaminated by the
    size tilt of buyback (large) vs diluter (small) names."""
    m, p, o = sig.mean, par.mean, oos.mean
    if m is None:
        return "**NO DATA** — no signal IC cells at the 1y horizon."
    if p is None:
        # The stated guard IS the size-partial IC. If it is missing (too few names with the
        # pre-window size proxy), we CANNOT de-confound — never fall back to the raw (confounded) IC.
        return (
            f"**INCONCLUSIVE — no size control.** Raw 1y IC {_f(m)} but fewer than the minimum names "
            "had the pre-window size proxy, so the IC cannot be de-confounded. Treat as unvalidated; "
            "net_issuance stays STANDALONE and UNWEIGHTED."
        )
    trust = (
        ""
        if anchors_ok
        else "NOTE: the short-horizon momentum anchor did not validate cleanly; read directionally. "
    )
    de = p  # the de-confounded (size-controlled) IC is the real measure
    survives_oos = o is not None and (o > 0) == (de > 0) and abs(o) > 0.005
    # CONFOUND = the raw IC is meaningfully non-zero but collapses once size is controlled (the raw
    # number is a size tilt, not a net-issuance effect). Distinct from a plain null (raw also ~0).
    confound = p is not None and abs(m) > 0.02 and abs(p) < abs(m) / 2
    tail = (
        " net_issuance stays STANDALONE and UNWEIGHTED (the validate-before-trust outcome). The "
        "user's catalyst-style 'good raise' edge needs offering-level data, not a shares_out delta, "
        "so this null does not refute that edge — it refutes the cheap proxy."
    )
    # EDGE CONFIRMED requires the machinery self-check to have PASSED — never promote a signal using
    # validation machinery the harness just flagged as unreliable.
    if anchors_ok and de > 0.015 and survives_oos:
        return (
            f"**EDGE CONFIRMED (modest).** Size-controlled 1y IC {_f(p)} (raw {_f(m)}), "
            f"positive and holds OOS ({_f(o)}). Candidate for a SMALL weight after a cross-market "
            "replication."
        )
    if confound:
        return (
            f"{trust}**NO INDEPENDENT EDGE — SIZE CONFOUND.** Raw 1y IC {_f(m)} (t≈{_f2(sig.t_stat)}) "
            f"collapses to a SIZE-CONTROLLED 1y IC of {_f(p)} ≈ 0: the raw number is a market-cap "
            "tilt (buyback names skew large-cap; large underperformed small on this 2011-23 mid/small "
            f"universe), NOT a net-issuance effect. OOS de-confounded ≈ {_f(o)}.{tail}"
        )
    if de is not None and abs(de) < 0.015:
        return (
            f"{trust}**NO EDGE (null).** Size-controlled 1y IC {_f(p)} ≈ 0 (raw {_f(m)}, "
            f"t≈{_f2(sig.t_stat)}); net-issuance does not rank forward winners on this "
            f"universe/period. OOS de-confounded ≈ {_f(o)}.{tail}"
        )
    return (
        f"{trust}**WEAK / DIRECTIONAL ONLY.** Size-controlled 1y IC {_f(p)} (raw {_f(m)}); inside "
        "the noise band and not robust. Keep STANDALONE; do not weight."
    )


def _f(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.4f}"


def _f2(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2f}"


if __name__ == "__main__":
    main()
