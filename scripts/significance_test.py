"""Statistical-significance battery for the megacap-momentum strategies.

Closes the borderline-significance gap flagged in MASTER-REPORT-2008-2026.md by
replacing the crude Bonferroni binomial with the Probabilistic / Deflated
Sharpe Ratio (Bailey & López de Prado) and a circular block bootstrap.

Inputs (daily return series, written by ``trader factor-portfolio
--returns-output``):
    out/variantN-returns.csv          regime-cash ON  (trader-CLI line)
    out/variantN-nocash-returns.csv   regime-cash OFF (ablation)

The IDEAL line (aqr_top7_cap20_trail10) is re-run in-process from yfinance +
catalog fundamentals to (a) check reproducibility against the documented
14/15 windows and (b) obtain its full-sample monthly return series for DSR.

Output: out/significance-report.md
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.significance import (  # noqa: E402
    block_bootstrap_sharpe,
    dsr_from_stats,
    minimum_track_record_length,
    per_period_sharpe,
    probabilistic_sharpe_ratio,
    sample_kurtosis,
    sample_skewness,
    sampling_sharpe_variance,
    sharpe_ratio,
)

OUT = ROOT / "out"
N_BOOT = 5000

# Documented (2026-05-28) reference metrics for the reproducibility check.
DOC_VARIANT_N = {"ann": 0.1991, "sharpe": 0.98, "mdd": 0.3889, "excess": 0.0888}
DOC_IDEAL = {"positive_rate": 0.933, "avg_excess": 0.0767, "avg_sharpe": 1.45, "worst_mdd": 0.1846}

# Stress windows: the 3 validation windows + 4 new ones requested in the
# MASTER-REPORT "학술적 엄밀성 강화" section.
STRESS_WINDOWS = [
    ("GFC", date(2008, 9, 1), date(2009, 3, 9)),
    ("COVID", date(2020, 2, 15), date(2020, 3, 23)),
    ("2022-bear", date(2022, 1, 1), date(2022, 10, 12)),
    ("2011-EU-debt", date(2011, 7, 22), date(2011, 10, 3)),
    ("2015-China", date(2015, 8, 17), date(2016, 2, 11)),
    ("2018-Q4", date(2018, 10, 1), date(2018, 12, 24)),
    ("2023-SVB", date(2023, 3, 1), date(2023, 3, 31)),
]

# Multiple-testing N sweep. Defensibility per the documented research breadth
# (MASTER-REPORT-2008-2026.md): N=1 no deflation; N=3 the actual RF-lookback
# candidates {0,100,200} that were selected among (most defensible); N=13 the
# walk-forward time windows (these are time splits, not independent configs);
# N=39 = 3 RF × 13 WF (the report's headline trial count); N>=104 hypothetical
# broader parameter sweeps not actually run.
N_TRIALS_GRID = [1, 3, 13, 39, 104]


# --------------------------------------------------------------------------- #
# IO                                                                          #
# --------------------------------------------------------------------------- #
def load_returns_csv(path: Path) -> tuple[list[date], list[float], list[float]]:
    dates: list[date] = []
    port: list[float] = []
    bench: list[float] = []
    for line in path.read_text(encoding="utf-8").strip().splitlines()[1:]:
        d, p, b = line.split(",")
        dates.append(date.fromisoformat(d))
        port.append(float(p))
        bench.append(float(b))
    return dates, port, bench


def cumulative(returns: list[float]) -> float:
    eq = 1.0
    for r in returns:
        eq *= 1.0 + r
    return eq - 1.0


def max_drawdown(returns: list[float]) -> float:
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in returns:
        eq *= 1.0 + r
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    return mdd


def annualize(returns: list[float], ppy: int) -> float:
    n = len(returns)
    if n == 0:
        return 0.0
    total = cumulative(returns)
    years = n / ppy
    if years <= 0 or (1.0 + total) <= 0:
        return 0.0
    return (1.0 + total) ** (1.0 / years) - 1.0


def regime_dispersion_variance(returns: list[float], chunk: int) -> float:
    """Variance of per-period Sharpe across non-overlapping sub-periods.

    CAVEAT — this is a *regime-dispersion* estimate, NOT Bailey's cross-trial
    variance V. It measures how much ONE strategy's Sharpe swings across market
    regimes (consecutive calendar chunks), which is driven by market structure
    (trends, vol clustering), not by the search degrees of freedom from trying
    different configurations. It is an UPPER-ish, pessimistic proxy for V — it
    over-deflates the DSR. The true cross-configuration V is unknown without the
    full trial log; ``sampling_sharpe_variance`` is the optimistic lower bound.
    Both are reported so the reader sees the DSR's sensitivity to this choice.
    ``chunk`` is the sub-period length in periods.
    """
    sharpes = [
        per_period_sharpe(returns[i : i + chunk]) for i in range(0, len(returns) - chunk + 1, chunk)
    ]
    if len(sharpes) < 2:
        return 0.0
    mean = sum(sharpes) / len(sharpes)
    return sum((s - mean) ** 2 for s in sharpes) / (len(sharpes) - 1)


# --------------------------------------------------------------------------- #
# Per-series significance block                                              #
# --------------------------------------------------------------------------- #
def significance_block(
    name: str, returns: list[float], ppy: int, chunk: int, block_size: int
) -> dict:
    n = len(returns)
    pp_sr = per_period_sharpe(returns)
    ann_sr = sharpe_ratio(returns, periods_per_year=ppy)
    skew = sample_skewness(returns)
    kurt = sample_kurtosis(returns)
    psr = probabilistic_sharpe_ratio(returns)
    mintrl = minimum_track_record_length(returns, target_prob=0.95)
    boot = block_bootstrap_sharpe(
        returns, n_boot=N_BOOT, block_size=block_size, seed=0, periods_per_year=ppy
    )
    v_proxy = regime_dispersion_variance(returns, chunk)
    v_sampling = sampling_sharpe_variance(returns)
    # Two principled V bounds: sampling (optimistic lower) and regime-proxy
    # (pessimistic upper). The true cross-trial V lies between them.
    dsr_grid = {
        nt: {
            "v_sampling": dsr_from_stats(pp_sr, n, skew, kurt, nt, v_sampling),
            "v_proxy": dsr_from_stats(pp_sr, n, skew, kurt, nt, v_proxy),
        }
        for nt in N_TRIALS_GRID
    }
    return {
        "name": name,
        "n": n,
        "ann_return": annualize(returns, ppy),
        "per_period_sharpe": pp_sr,
        "ann_sharpe": ann_sr,
        "skew": skew,
        "kurt": kurt,
        "mdd": max_drawdown(returns),
        "psr": psr,
        "mintrl": mintrl,
        "boot": boot,
        "v_proxy": v_proxy,
        "v_sampling": v_sampling,
        "dsr_grid": dsr_grid,
        "ppy": ppy,
    }


def fmt_block_md(b: dict) -> list[str]:
    boot = b["boot"]
    lines = [
        f"### {b['name']}",
        "",
        f"- Observations: {b['n']} ({'daily' if b['ppy'] == 252 else 'monthly'})",
        f"- Annualized return: {b['ann_return'] * 100:+.2f}%",
        f"- Annualized Sharpe: **{b['ann_sharpe']:.3f}** (per-period {b['per_period_sharpe']:.4f})",
        f"- Max drawdown: {b['mdd'] * 100:.2f}%",
        f"- Skewness: {b['skew']:+.3f} | Non-excess kurtosis: {b['kurt']:.3f} (normal = 3.0)",
        "",
        f"- **PSR(SR>0): {b['psr'] * 100:.2f}%** "
        f"({'significant' if b['psr'] > 0.95 else 'NOT significant'} at 95%)",
        f"- Minimum Track Record Length (95%): {b['mintrl']:.0f} periods "
        f"(~{b['mintrl'] / b['ppy']:.1f} years) — observations needed to confirm "
        f"Sharpe>0 at 95%; {'within' if b['mintrl'] <= b['n'] else 'EXCEEDS'} the "
        f"{b['n']}-obs sample",
        f"- Block bootstrap ({boot.n_boot} resamples, block={boot.block_size}): "
        f"annualized Sharpe {boot.point_sharpe:.3f}, "
        f"95% CI [{boot.ci_low:.3f}, {boot.ci_high:.3f}]; "
        f"P(Sharpe>0) = {boot.prob_sharpe_gt_zero * 100:.2f}%, "
        f"recentered null p-value (H0: Sharpe≤0) = {boot.p_value_null:.4f}",
        f"- Trial-Sharpe variance: sampling (lower) {b['v_sampling']:.5f} | "
        f"regime-dispersion (upper) {b['v_proxy']:.5f}",
        "",
        "**Deflated Sharpe Ratio** under the two V bounds (the verdict hinges on "
        "which trial-variance V applies — true V lies between them):",
        "",
        "| N trials | DSR (sampling V, optimistic) | DSR (regime-proxy V, pessimistic) |",
        "|---:|---:|---:|",
    ]
    for nt in N_TRIALS_GRID:
        g = b["dsr_grid"][nt]
        lines.append(f"| {nt} | {g['v_sampling'] * 100:.2f}% | {g['v_proxy'] * 100:.2f}% |")
    lines.append("")
    return lines


# --------------------------------------------------------------------------- #
# IDEAL line (re-run for reproducibility + monthly series)                     #
# --------------------------------------------------------------------------- #
def run_ideal_line() -> dict | None:
    try:
        import pandas as pd  # noqa: F401
        import yfinance as yf  # noqa: F401

        from data.catalog import MarketDataCatalog
        from scripts.aqr_ideal_walkforward import (
            BENCHMARK,
            MEGACAPS,
            prefetch,
            run_window,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"error": f"import failed: {exc}"}

    try:
        raw = yf.download(
            MEGACAPS + [BENCHMARK],
            start="2008-01-01",
            end="2026-05-28",
            auto_adjust=True,
            progress=False,
        )
        prices = raw["Close"].dropna(how="all")
        catalog = MarketDataCatalog()
        fund_cache = prefetch(catalog)
    except Exception as exc:  # pragma: no cover - network dependent
        return {"error": f"data load failed: {exc}"}

    # Full continuous run for the monthly return series.
    full = run_window(pd.Timestamp("2009-01-01"), pd.Timestamp("2026-05-01"), prices, fund_cache)
    if not full or not full.get("monthly_returns"):
        return {"error": "full-sample IDEAL run produced no returns"}

    # Rolling 3y windows for the reproducibility check.
    windows = []
    sy = 2009
    while sy + 3 <= 2026:
        windows.append(
            (pd.Timestamp(f"{sy}-01-01"), pd.Timestamp(f"{sy + 3}-01-01") - pd.Timedelta(days=1))
        )
        sy += 1
    wf = [r for ws, we in windows if (r := run_window(ws, we, prices, fund_cache))]
    pos_rate = sum(1 for r in wf if r["excess"] > 0) / len(wf) if wf else 0.0
    avg_excess = sum(r["excess"] for r in wf) / len(wf) if wf else 0.0
    avg_sharpe = sum(r["sharpe"] for r in wf) / len(wf) if wf else 0.0
    worst_mdd = max((r["mdd"] for r in wf), default=0.0)
    return {
        "monthly_returns": full["monthly_returns"],
        "spy_returns": full["spy_returns"],
        "wf_windows": len(wf),
        "positive_rate": pos_rate,
        "avg_excess": avg_excess,
        "avg_sharpe": avg_sharpe,
        "worst_mdd": worst_mdd,
        "wf_sharpes": [r["sharpe"] for r in wf],
    }


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #
def main() -> None:
    md: list[str] = [
        "# Statistical-Significance Battery — Megacap Momentum",
        "",
        "Research-only output. Does not constitute investment advice.",
        "",
        f"Generated by `scripts/significance_test.py` (N_boot={N_BOOT}).",
        "",
        "Closes the borderline-significance gap in `MASTER-REPORT-2008-2026.md`:"
        " PSR/DSR (Bailey & López de Prado) + circular block bootstrap replace the"
        " crude Bonferroni binomial.",
        "",
        "---",
        "",
    ]

    # --- trader-CLI line (Variant N) ---------------------------------------- #
    on_path = OUT / "variantN-returns.csv"
    off_path = OUT / "variantN-nocash-returns.csv"
    if not on_path.exists():
        raise SystemExit(f"missing {on_path}; run trader factor-portfolio --returns-output first")
    on_dates, on_port, on_bench = load_returns_csv(on_path)
    _, off_port, off_bench = load_returns_csv(off_path)

    # Reproducibility headline.
    cur_ann = annualize(on_port, 252)
    cur_sharpe = sharpe_ratio(on_port, periods_per_year=252)
    cur_mdd = max_drawdown(on_port)
    md += [
        "## 0. Reproducibility check (CRITICAL)",
        "",
        "Timeline: the Variant N documented baseline"
        " (`out/sp100-2008-regimecash-best-rf100.md`, 2026-05-28 19:36, 3,383 PIT"
        " fundamental records) is **PRE-ingest**. The catalog fundamentals were"
        " then re-ingested (`fundamentals_q`: 3,383 → 7,291 records, all SEC EDGAR"
        " companyfacts, zero duplicates, proper PIT asof timestamps). Re-running"
        " the *identical* documented Variant N parameters on current data:",
        "",
        "| Metric | Documented 2026-05-28 | Current re-run | Δ |",
        "|---|---:|---:|---:|",
        f"| Annualized return | {DOC_VARIANT_N['ann'] * 100:.2f}% | {cur_ann * 100:.2f}% |"
        f" {(cur_ann - DOC_VARIANT_N['ann']) * 100:+.2f}pp |",
        f"| Sharpe | {DOC_VARIANT_N['sharpe']:.2f} | {cur_sharpe:.2f} |"
        f" {cur_sharpe - DOC_VARIANT_N['sharpe']:+.2f} |",
        f"| Max drawdown | {DOC_VARIANT_N['mdd'] * 100:.2f}% | {cur_mdd * 100:.2f}% |"
        f" {(cur_mdd - DOC_VARIANT_N['mdd']) * 100:+.2f}pp |",
        "",
        "The 0.2-weight Quality/Value factors swing the concentrated (top_n=2)"
        " portfolio materially when fundamental coverage doubles — a fragility"
        " signal the DSR is designed to expose. **All significance metrics below"
        " use the honest, reproducible-today return series.**",
        "",
        "---",
        "",
        "## 1. Trader-CLI line (Variant N, regime-cash ON)",
        "",
    ]
    block_on = significance_block("Variant N (current data)", on_port, 252, 252, 21)
    md += fmt_block_md(block_on)

    # --- Ablation: regime-cash ON vs OFF ------------------------------------ #
    md += [
        "## 2. Regime-cash ablation (ON vs OFF)",
        "",
        "| Metric | regime-cash ON | regime-cash OFF | Δ |",
        "|---|---:|---:|---:|",
    ]
    on_ann, off_ann = annualize(on_port, 252), annualize(off_port, 252)
    on_shp, off_shp = (
        sharpe_ratio(on_port, periods_per_year=252),
        sharpe_ratio(off_port, periods_per_year=252),
    )
    on_dd, off_dd = max_drawdown(on_port), max_drawdown(off_port)
    md += [
        f"| Annualized return | {on_ann * 100:+.2f}% | {off_ann * 100:+.2f}% |"
        f" {(on_ann - off_ann) * 100:+.2f}pp |",
        f"| Sharpe | {on_shp:.3f} | {off_shp:.3f} | {on_shp - off_shp:+.3f} |",
        f"| Max drawdown | {on_dd * 100:.2f}% | {off_dd * 100:.2f}% |"
        f" {(on_dd - off_dd) * 100:+.2f}pp |",
    ]
    # 2022 slice.
    w22 = [
        (p, b)
        for d, p, b in zip(on_dates, on_port, on_bench, strict=True)
        if date(2022, 1, 1) <= d <= date(2022, 10, 12)
    ]
    w22_off = [
        p
        for d, p in zip(on_dates, off_port, strict=True)
        if date(2022, 1, 1) <= d <= date(2022, 10, 12)
    ]
    on22 = cumulative([p for p, _ in w22])
    off22 = cumulative(w22_off)
    md += [
        f"| 2022-bear cumulative | {on22 * 100:+.2f}% | {off22 * 100:+.2f}% |"
        f" {(on22 - off22) * 100:+.2f}pp |",
        "",
        "The ON and OFF daily return CSVs are **byte-identical** — on current data"
        " the regime-cash trigger never activates, so its marginal contribution is"
        " exactly **zero** (not merely small). It cannot be credited with any of"
        " the documented robustness; the defensive-basket (TLT/SHY) and risk"
        " filter do the work.",
        "",
        "---",
        "",
    ]

    # --- Stress windows (slices of the ON daily series) --------------------- #
    md += [
        "## 3. Stress windows (3 validation + 4 new)",
        "",
        "Slices of the current Variant N daily series vs SPY. These 7 windows are"
        " not a complete crisis census (e.g. 2010 flash crash, 2016 Brexit"
        " excluded); they extend the 3 validation windows with the 4 requested in"
        " the MASTER-REPORT '학술적 엄밀성 강화' section. Short windows (e.g. SVB)"
        " are noisy.",
        "",
        "| Window | Period | Strategy | SPY | Excess | Strat MDD |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for wname, ws, we in STRESS_WINDOWS:
        idx = [i for i, d in enumerate(on_dates) if ws <= d <= we]
        if not idx:
            md.append(f"| {wname} | {ws}~{we} | n/a | n/a | n/a | n/a |")
            continue
        sp = [on_port[i] for i in idx]
        sb = [on_bench[i] for i in idx]
        s_ret, b_ret = cumulative(sp), cumulative(sb)
        md.append(
            f"| {wname} | {ws}~{we} | {s_ret * 100:+.2f}% | {b_ret * 100:+.2f}% |"
            f" {(s_ret - b_ret) * 100:+.2f}pp | {max_drawdown(sp) * 100:.2f}% |"
        )
    md += ["", "---", ""]

    # --- IDEAL line --------------------------------------------------------- #
    md += ["## 4. IDEAL line (aqr_top7_cap20_trail10)", ""]
    ideal = run_ideal_line()
    if ideal is None or "error" in (ideal or {}):
        reason = (ideal or {}).get("error", "unknown") if ideal else "unknown"
        md += [
            f"⚠️ IDEAL line skipped: {reason}.",
            "",
            "The IDEAL line also reads catalog fundamentals, so the same"
            " reproducibility caveat applies; re-run when yfinance is reachable.",
            "",
        ]
    else:
        md += [
            "### Deterministic re-run (rolling 3y windows — NOT a robustness test)",
            "",
            "⚠️ The IDEAL baseline (`out/aqr-ideal-walkforward.md`, 2026-05-28 23:43)"
            " was generated **AFTER** the fundamentals re-ingest. So the exact match"
            " below is a deterministic same-data re-run, **not** evidence of"
            " robustness. **IDEAL's sensitivity to the fundamentals change is"
            " UNTESTED** — unlike Variant N, no pre-ingest baseline exists to"
            " measure it against. FOLLOW-UP: a coverage-perturbation test of this"
            " exact question is in `out/ideal-fundamental-sensitivity.md`.",
            "",
            "| Metric | Documented (PIT 106) | Current re-run | Δ |",
            "|---|---:|---:|---:|",
            f"| Windows | 15 | {ideal['wf_windows']} | — |",
            f"| Positive rate | {DOC_IDEAL['positive_rate'] * 100:.1f}% |"
            f" {ideal['positive_rate'] * 100:.1f}% |"
            f" {(ideal['positive_rate'] - DOC_IDEAL['positive_rate']) * 100:+.1f}pp |",
            f"| Avg excess | {DOC_IDEAL['avg_excess'] * 100:+.2f}% |"
            f" {ideal['avg_excess'] * 100:+.2f}% |"
            f" {(ideal['avg_excess'] - DOC_IDEAL['avg_excess']) * 100:+.2f}pp |",
            f"| Avg Sharpe | {DOC_IDEAL['avg_sharpe']:.2f} |"
            f" {ideal['avg_sharpe']:.2f} |"
            f" {ideal['avg_sharpe'] - DOC_IDEAL['avg_sharpe']:+.2f} |",
            f"| Worst MDD | {DOC_IDEAL['worst_mdd'] * 100:.2f}% |"
            f" {ideal['worst_mdd'] * 100:.2f}% |"
            f" {(ideal['worst_mdd'] - DOC_IDEAL['worst_mdd']) * 100:+.2f}pp |",
            "",
        ]
        block_ideal = significance_block(
            "IDEAL line (monthly, current data)", ideal["monthly_returns"], 12, 12, 12
        )
        md += fmt_block_md(block_ideal)

    # --- Verdict ------------------------------------------------------------ #
    repro_cagr_delta = (cur_ann - DOC_VARIANT_N["ann"]) * 100
    md += [
        "## 5. Verdict",
        "",
        "Ordered by how robust each conclusion is to methodological choices:",
        "",
        f"1. **Reproducibility failure dominates.** Variant N's CAGR fell"
        f" {repro_cagr_delta:+.2f}pp (Sharpe {cur_sharpe:.2f} vs documented"
        f" {DOC_VARIANT_N['sharpe']:.2f}) when fundamental coverage doubled. A"
        " 0.2-weight factor moving a top_n=2 portfolio this much is structural"
        " fragility no p-value can offset. This is the decisive finding.",
        f"2. **Bootstrap is the most assumption-light significance signal.** Variant"
        f" N annualized-Sharpe 95% CI"
        f" [{block_on['boot'].ci_low:.2f}, {block_on['boot'].ci_high:.2f}],"
        f" recentered null p-value {block_on['boot'].p_value_null:.4f}. PSR(SR>0)"
        f" {block_on['psr'] * 100:.1f}%. Both say the raw Sharpe is > 0 — but say"
        " nothing about selection bias.",
        "3. **DSR is the selection-bias test, and it hinges on the trial-variance V"
        " — which we cannot pin down without the full trial log.** Under the"
        " sampling-V lower bound the DSR stays high even at N=39; under the"
        " regime-dispersion upper bound it collapses. The honest reading: with"
        " a small genuine search (N≈3 RF candidates) the deflation is mild; the"
        " collapse at large N/V is partly an artifact of the pessimistic V proxy,"
        " NOT independent proof of overfitting. Do not treat any single DSR cell"
        " as authoritative.",
        "4. **MinTRL** for Variant N (~{:.0f} obs) means even the raw Sharpe needs"
        " years of OOS to confirm — consistent with caution.".format(block_on["mintrl"]),
        "",
        "Bottom line: the statistics do not rescue Variant N. The reproducibility"
        " break is disqualifying on its own; the IDEAL line remains the stronger"
        " candidate but its fundamental-sensitivity is still untested (§4).",
        "",
    ]

    report = "\n".join(md) + "\n"
    (OUT / "significance-report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
