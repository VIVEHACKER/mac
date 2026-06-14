"""Defensive overlay on the IDEAL candidate — does an SPY-200d-MA crash filter help?

The stress-window evidence showed the long-only candidate falls WITH (sometimes more
than) SPY in crises (2018 Q4 excess −5.40%). The standard long-only crash protection is
a trend filter (Faber): when SPY is below its 200-day MA at a rebalance, step out of the
book into cash for that month. This script tests whether that overlay improves crisis
behaviour WITHOUT destroying the validated walk-forward edge.

Discipline (same as the breadth program):
  * ONE pre-declared rule, no grid: MA window 200d, defensive exposure 0 (full cash),
    regime read AT the rebalance (no look-ahead).
  * Switch cost charged on every regime TOGGLE (full exit/re-entry = 2x one-way fee),
    so the hedge is not free.
  * Overlay is applied POST-HOC to the validated run_window monthly series (cash-out
    months → 0 return), so the base engine and its numbers are untouched.

Pre-declared adoption bars (declared 2026-06-14, before the run):
  ADOPT the overlay only if BOTH:
    (1) worst stress-window total return improves by >= 5 pp vs base (the point), AND
    (2) the walk-forward edge survives: avg test excess still > 0 AND positive rate >= 60%.
  Sharpe/return trade-off is reported but not a gate. Anything less → NOT adopted; the
  base candidate stands. Claims are earned via the gate; nothing here wires capital.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot  # noqa: E402
from scripts.aqr_ideal_grid import DEFAULT_PRICES, DEFAULT_SNAPSHOT  # noqa: E402
from scripts.aqr_ideal_walkforward import BENCHMARK, prefetch, run_window  # noqa: E402

OUT_DIR = ROOT / "out"
MA_WINDOW = 200  # trading days, pre-declared (no grid)
DEFENSIVE_EXPOSURE = 0.0  # full cash when SPY < MA200
SWITCH_FEE_BPS = 10.0  # charged on a regime toggle (full turnover both ways)

STRESS_WINDOWS = [
    ("2018 Q4 selloff", "2018-09-01", "2018-12-31"),
    ("COVID 2020-02→2020-06", "2020-02-01", "2020-06-30"),
    ("2022 bear", "2022-01-01", "2022-10-31"),
]


def _ma_regime(prices: pd.DataFrame, dates: list[str]) -> list[bool]:
    """risk_on[i] = SPY close > its trailing 200d MA at rebalance date i (known at i)."""
    spy = prices[BENCHMARK].dropna()
    ma = spy.rolling(MA_WINDOW).mean()
    out: list[bool] = []
    for d in dates:
        ts = pd.Timestamp(d)
        if ts not in spy.index or pd.isna(ma.loc[ts]):
            out.append(True)  # insufficient history -> stay invested (no spurious hedge)
        else:
            out.append(bool(spy.loc[ts] > ma.loc[ts]))
    return out


def _overlay(result: dict, prices: pd.DataFrame) -> dict:
    """Apply the cash-out overlay to a run_window result; recompute metrics from the series."""
    monthly = list(result["monthly_returns"])
    spy = list(result["spy_returns"])
    regime = _ma_regime(prices, result["dates"])
    overlaid: list[float] = []
    prev_on = True
    for i, base_ret in enumerate(monthly):
        on = regime[i] if i < len(regime) else True
        ret = base_ret if on else DEFENSIVE_EXPOSURE * base_ret  # cash = 0 return
        if on != prev_on:  # toggled regime -> full round-trip cost
            ret -= SWITCH_FEE_BPS / 1e4
        overlaid.append(ret)
        prev_on = on
    return _metrics(overlaid, spy, result["start"], result["end"])


def _metrics(monthly: list[float], spy: list[float], start: str, end: str) -> dict:
    if not monthly:
        return {}
    eq = sp = 1.0
    curve = []
    for r, s in zip(monthly, spy, strict=False):
        eq *= 1 + r
        sp *= 1 + s
        curve.append(eq)
    years = len(monthly) / 12.0
    ann = eq ** (1 / years) - 1
    spy_ann = sp ** (1 / years) - 1
    mr = pd.Series(monthly)
    peak = pd.Series(curve).cummax()
    mdd = float(((peak - pd.Series(curve)) / peak).max())
    return {
        "start": start,
        "end": end,
        "ann": ann,
        "spy_ann": spy_ann,
        "excess": ann - spy_ann,
        "sharpe": (mr.mean() / mr.std()) * math.sqrt(12.0) if mr.std() > 0 else 0.0,
        "mdd": mdd,
        "total_return": eq - 1.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="IDEAL defensive (200d MA) overlay gate.")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--fee-bps", type=float, default=0.0)
    parser.add_argument("--output", type=Path, default=OUT_DIR / "ideal-defensive-walkforward.md")
    args = parser.parse_args()

    prices = read_price_snapshot(args.prices, verify=True)
    fund_cache = prefetch(None, snapshot_path=args.snapshot)
    cfg = {"fee_bps": args.fee_bps}

    windows = []
    y = 2009
    while y + 3 <= 2026:
        windows.append((pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y + 2}-12-31")))
        y += 1

    base_rows, def_rows = [], []
    for ws, we in windows:
        base = run_window(ws, we, prices, fund_cache, cfg=cfg)
        if base is None:
            continue
        base_rows.append(base)
        def_rows.append(_overlay(base, prices))

    def agg(rows: list[dict], key: str) -> float:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0.0

    base_pos = sum(1 for r in base_rows if r["excess"] > 0) / len(base_rows) * 100
    def_pos = sum(1 for r in def_rows if r["excess"] > 0) / len(def_rows) * 100

    # Stress windows: base vs overlay total return.
    stress = []
    for name, s, e in STRESS_WINDOWS:
        b = run_window(pd.Timestamp(s), pd.Timestamp(e), prices, fund_cache, cfg=cfg)
        if b is None:
            continue
        d = _overlay(b, prices)
        b_tr = math.prod(1 + x for x in b["monthly_returns"]) - 1
        stress.append((name, b_tr, d["total_return"]))
    base_worst = min((b for _, b, _ in stress), default=0.0)
    def_worst = min((d for _, _, d in stress), default=0.0)

    bar1 = (def_worst - base_worst) >= 0.05
    bar2 = agg(def_rows, "excess") > 0 and def_pos >= 60.0
    adopt = bar1 and bar2
    verdict = (
        "ADOPT-CANDIDATE — the 200d-MA overlay improves worst-stress by >= 5pp AND keeps "
        "the walk-forward edge (excess>0, positive>=60%). Proceed to PBO/fee-stress + a "
        "fresh paper-OOS track before any capital."
        if adopt
        else "NOT ADOPTED — overlay fails a pre-declared bar (see below). Base candidate stands."
    )

    pct = lambda x: f"{x * 100:+.2f}%"  # noqa: E731
    lines = [
        "# IDEAL defensive overlay (SPY 200d-MA cash filter) — walk-forward gate",
        "",
        f"Pinned {args.prices.name} | fee {args.fee_bps:.0f}bps | switch cost {SWITCH_FEE_BPS:.0f}bps/toggle | "
        f"rule: cash when SPY < {MA_WINDOW}d MA (no grid)",
        "",
        "## Walk-forward (15 windows) — base vs defensive",
        "",
        "| metric | base | defensive |",
        "|---|---:|---:|",
        f"| positive test rate | {base_pos:.1f}% | {def_pos:.1f}% |",
        f"| avg test excess | {pct(agg(base_rows, 'excess'))} | {pct(agg(def_rows, 'excess'))} |",
        f"| avg test Sharpe | {agg(base_rows, 'sharpe'):.2f} | {agg(def_rows, 'sharpe'):.2f} |",
        f"| worst test MDD | {pct(max(r['mdd'] for r in base_rows))} | {pct(max(r['mdd'] for r in def_rows))} |",
        "",
        "## Stress windows — total return base vs defensive",
        "",
        "| crisis | base | defensive | improvement |",
        "|---|---:|---:|---:|",
    ]
    for name, b, d in stress:
        lines.append(f"| {name} | {pct(b)} | {pct(d)} | {pct(d - b)} |")
    lines += [
        "",
        f"worst stress: base {pct(base_worst)} → defensive {pct(def_worst)} "
        f"(improvement {pct(def_worst - base_worst)}; bar1 needs >= +5.00pp)",
        "",
        "## Verdict (bars pre-declared in the module docstring)",
        "",
        verdict,
        "",
        "## Honest caveats",
        "- The 200d MA is a single pre-declared rule, but it IS a researcher choice; the",
        "  out-of-sample case rests on the published Faber literature, not on this fit.",
        "- Overlay is post-hoc on monthly returns (cash month = 0); intramonth path and",
        "  exact switch slippage beyond the flat toggle cost are not modelled.",
        "- MA filters trade bull-market return for crash protection — read BOTH columns,",
        "  not just the stress improvement.",
    ]
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
