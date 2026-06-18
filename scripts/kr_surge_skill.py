"""Phase B — does the quality-gated momentum/volume surge screen have edge on KR stocks?

Measures the screener's predictive skill on a liquid KOSPI universe (top-N by market cap =
the non-junk set), walk-forward, using FREE FinanceDataReader data (no KRX login/payment).

CAVEATS (declared up front, same honesty bar as the rest of this repo):
  - UNPINNED: fetched live from fdr, not a content-hashed snapshot — not byte-reproducible.
  - SURVIVORSHIP-INFLATED: "top-N by current market cap" is today's winners; delisted/faded
    names are absent, so any measured edge is optimistic.
  - Single pre-declared definition (60d momentum, 5/20 volume surge); no parameter tuning.
This is a FIRST READ to decide whether the screen is worth pursuing — not a deploy gate. A
positive result would still need a pinned, survivorship-controlled re-validation before trust.

Read: top-K (by screen score, among surge>=threshold survivors) forward return vs the universe,
and rank-IC of the composite score vs forward return. ~0 IC / top<=universe => no edge (matching
the prior US momentum-picking negatives), and we say so plainly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.models import PriceBar  # noqa: E402
from engine.screener import _momentum, _volume_surge, _z_scores  # noqa: E402

FWD_BARS = 21
MOM_LOOKBACK = 60
SURGE_WINDOW = 5
BASE_WINDOW = 20
SURGE_MIN = 1.5
TOP_K = 10
MIN_BARS = MOM_LOOKBACK + 1


def _fetch_bars(codes: list[str], start: str, end: str) -> dict[str, list[PriceBar]]:
    import FinanceDataReader as fdr  # noqa: N813

    out: dict[str, list[PriceBar]] = {}
    for code in codes:
        try:
            df = fdr.DataReader(code, start, end)
        except Exception:
            continue
        if df is None or df.empty or "Volume" not in df.columns:
            continue
        bars = [
            PriceBar(
                symbol=code,
                market="kr",
                source_symbol=code,
                ts=idx.date(),
                open=float(r.Open),
                high=float(r.High),
                low=float(r.Low),
                close=float(r.Close),
                volume=float(r.Volume),
                currency="KRW",
            )
            for idx, r in df.iterrows()
            if r.Close > 0
        ]
        if len(bars) >= MIN_BARS + FWD_BARS:
            out[code] = bars
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="KR surge-screen edge (Phase B, first read).")
    parser.add_argument("--top-universe", type=int, default=120, help="top-N KOSPI by market cap")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2026-05-31")
    parser.add_argument("--output", type=Path, default=ROOT / "out" / "kr-surge-skill.md")
    args = parser.parse_args()

    import FinanceDataReader as fdr  # noqa: N813

    listing = fdr.StockListing("KOSPI")
    listing = listing[listing["Marcap"].notna()].sort_values("Marcap", ascending=False)
    codes = [str(c) for c in listing["Code"].head(args.top_universe).tolist()]
    print(f"universe: top {len(codes)} KOSPI by market cap; fetching {args.start}..{args.end} ...")
    bars_by = _fetch_bars(codes, args.start, args.end)
    print(f"fetched {len(bars_by)} names with sufficient history")
    if len(bars_by) < 20:
        print("insufficient data fetched")
        return 1

    # Union of trading dates (use the longest series as the calendar).
    calendar = sorted({b.ts for bars in bars_by.values() for b in bars})
    cal_index = {d: i for i, d in enumerate(calendar)}
    # Monthly rebalance dates = last calendar date in each month.
    by_month: dict[tuple[int, int], object] = {}
    for d in calendar:
        by_month[(d.year, d.month)] = d
    rebal_dates = sorted(by_month.values())

    top_rets: list[float] = []
    uni_rets: list[float] = []
    bot_rets: list[float] = []
    ics: list[float] = []
    months = 0
    for rebal in rebal_dates:
        ri = cal_index[rebal]
        if ri + FWD_BARS >= len(calendar):
            break
        fwd_date = calendar[ri + FWD_BARS]
        scored: list[tuple[str, float, float, float]] = []  # code, mom, surge, fwd
        for code, bars in bars_by.items():
            upto = [b for b in bars if b.ts <= rebal]
            if len(upto) < MIN_BARS:
                continue
            fut = [b for b in bars if b.ts == fwd_date]
            if not fut:
                continue
            mom = _momentum(upto, MOM_LOOKBACK)
            surge = _volume_surge(upto, SURGE_WINDOW, BASE_WINDOW)
            fwd = fut[0].close / upto[-1].close - 1.0
            scored.append((code, mom, surge, fwd))
        if len(scored) < 20:
            continue
        months += 1
        mom_z = _z_scores([s[1] for s in scored])
        surge_z = _z_scores([s[2] for s in scored])
        composite = [mom_z[i] + surge_z[i] for i in range(len(scored))]
        fwds = [s[3] for s in scored]
        uni_rets.append(sum(fwds) / len(fwds))
        ic = pd.Series(composite).rank().corr(pd.Series(fwds).rank())
        if not pd.isna(ic):
            ics.append(float(ic))
        # gated surge candidates ranked by composite score
        survivors = sorted(
            [(composite[i], scored[i][3]) for i in range(len(scored)) if scored[i][2] >= SURGE_MIN],
            key=lambda x: x[0],
            reverse=True,
        )
        if len(survivors) >= TOP_K:
            top_rets.append(sum(r for _, r in survivors[:TOP_K]) / TOP_K)
            bot_rets.append(sum(r for _, r in survivors[-TOP_K:]) / TOP_K)

    def avg(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    mean_ic = avg(ics)
    ic_t = mean_ic * (len(ics) ** 0.5) if ics else 0.0
    top_u = avg(top_rets) - avg(uni_rets)
    pct = lambda x: f"{x * 100:+.2f}%"  # noqa: E731
    has_edge = mean_ic > 0.02 and ic_t > 2.0 and avg(top_rets) > avg(uni_rets)
    verdict = (
        f"EDGE CANDIDATE (caveated) — surge-screen rank-IC {mean_ic:+.3f} (t≈{ic_t:.1f}) and top-K "
        f"beats universe by {pct(top_u)}/period. Next: PIN a survivorship-controlled KR snapshot "
        "and re-validate before any trust/capital."
        if has_edge
        else f"NO RELIABLE EDGE — rank-IC {mean_ic:+.3f} (t≈{ic_t:.1f}), top-K minus universe "
        f"{pct(top_u)}/period. Consistent with the prior US momentum-picking negatives: the "
        "surge screen does not reliably rank forward returns. Honest conclusion: advisory-only, "
        "do NOT treat as a validated signal."
    )
    lines = [
        "# KR surge-screen edge (Phase B, first read — UNPINNED, survivorship-inflated)",
        "",
        f"Universe: top {len(bars_by)} KOSPI by market cap | {months} monthly rebalances "
        f"{args.start}..{args.end} | {FWD_BARS}-bar forward | surge>= {SURGE_MIN}x, top-{TOP_K}",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| mean rank-IC | **{mean_ic:+.3f}** (t≈{ic_t:.1f}) |",
        f"| top-{TOP_K} fwd | {pct(avg(top_rets))} |",
        f"| universe fwd | {pct(avg(uni_rets))} |",
        f"| bottom-{TOP_K} fwd | {pct(avg(bot_rets))} |",
        f"| top − universe | {pct(top_u)} |",
        "",
        "## Verdict (pre-declared, caveated)",
        "",
        verdict,
        "",
        "## Caveats",
        "- UNPINNED (live fdr) + SURVIVORSHIP (current top-N market cap) => optimistic.",
        "- Single definition (60d momentum, 5/20 surge); no tuning. First read, not a gate.",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
