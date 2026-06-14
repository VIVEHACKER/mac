"""Does RESIDUAL (beta-adjusted) momentum pick better than RAW momentum?

The skill measurement found the AQR composite has a near-zero rank-IC and raw momentum
crashes with the market (2018 Q4). Residual momentum — momentum of the part of a
stock's move NOT explained by its market beta (Blitz-Huij-Martens 2011) — is the
standard, economically-motivated fix: it strips the common market factor so the
ranking reflects idiosyncratic strength, and it crashes less. This tests that
hypothesis apples-to-apples against raw momentum on the same names/period.

Definitions (DECLARED 2026-06-14, before the run — no lookback grid):
  raw_mom[i]   = P[t] / P[t-126] - 1                                  (126 trading days)
  beta[i]      = cov(daily r_name, daily r_SPY) / var(daily r_SPY)    (same 126d window)
  resid_mom[i] = raw_mom[i] - beta[i] * spy_mom[i]                    (market-stripped)

Both rankings are scored with the picking_skill harness (21-day forward, top/bottom-7,
universe mean, Spearman rank-IC) over the same rebalances. Pre-declared verdict:
  ADOPT-CANDIDATE (residual replaces the raw-momentum leg) only if BOTH
    (1) mean rank-IC(residual) > rank-IC(raw), AND
    (2) top−universe spread(residual) > spread(raw).
  Else NO IMPROVEMENT — raw momentum stands; no further variants are tried (searching
  until one wins is data-mining). Read-only evidence; nothing is wired to capital.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot  # noqa: E402
from scripts.aqr_ideal_grid import DEFAULT_PRICES  # noqa: E402
from scripts.aqr_ideal_walkforward import BENCHMARK, MEGACAPS  # noqa: E402
from scripts.picking_skill import _fwd_return  # noqa: E402

OUT_DIR = ROOT / "out"
LOOKBACK = 126
FWD_BARS = 21
TOP_N = 7
MIN_OBS = 100  # min daily returns in the window for a usable beta


def _signals(
    prices: pd.DataFrame, sym: str, rebal: pd.Timestamp, spy_mom: float
) -> tuple[float, float] | None:
    """(raw_mom, resid_mom) for one name at a rebalance, or None if history is short."""
    px = prices[sym].loc[:rebal].dropna()
    spy = prices[BENCHMARK].loc[:rebal].dropna()
    if len(px) <= LOOKBACK or len(spy) <= LOOKBACK:
        return None
    p_now, p_then = float(px.iloc[-1]), float(px.iloc[-1 - LOOKBACK])
    if p_then <= 0:
        return None
    raw = p_now / p_then - 1.0
    # Daily-return regression over the trailing window (aligned dates).
    r_name = px.pct_change().iloc[-LOOKBACK:]
    r_spy = spy.pct_change().reindex(r_name.index)
    pair = pd.concat([r_name, r_spy], axis=1).dropna()
    if len(pair) < MIN_OBS:
        return None
    var = float(pair.iloc[:, 1].var())
    if var <= 0:
        return None
    beta = float(pair.iloc[:, 0].cov(pair.iloc[:, 1]) / var)
    return raw, raw - beta * spy_mom


def _bucket(scored: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """scored = (signal, fwd) sorted best-first → (top, universe, bottom, rank-IC)."""
    scored = sorted(scored, key=lambda x: x[0], reverse=True)
    fwds = [x[1] for x in scored]
    top = sum(fwds[:TOP_N]) / TOP_N
    bot = sum(fwds[-TOP_N:]) / TOP_N
    uni = sum(fwds) / len(fwds)
    ic = pd.Series([x[0] for x in scored]).rank().corr(pd.Series(fwds).rank())
    return top, uni, bot, (float(ic) if not pd.isna(ic) else 0.0)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Residual vs raw momentum selection skill.")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--output", type=Path, default=OUT_DIR / "residual-momentum-skill.md")
    args = parser.parse_args()

    prices = read_price_snapshot(args.prices, verify=True)
    last = pd.Timestamp(prices.index.max())
    rebal_dates = []
    cur = pd.Timestamp("2009-01-01") + pd.offsets.MonthEnd(0)
    while cur <= last:
        valid = prices.index[prices.index <= cur]
        if len(valid):
            rebal_dates.append(valid[-1])
        cur += pd.offsets.MonthEnd(1)

    raw_stats: list[tuple[float, float, float, float]] = []
    res_stats: list[tuple[float, float, float, float]] = []
    months = 0
    for rebal in rebal_dates:
        future = prices.index[prices.index > rebal][:FWD_BARS]
        if len(future) < FWD_BARS:
            break
        end_ts = future[-1]
        spy_then = prices[BENCHMARK].loc[:rebal].dropna()
        if len(spy_then) <= LOOKBACK:
            continue
        spy_mom = float(spy_then.iloc[-1]) / float(spy_then.iloc[-1 - LOOKBACK]) - 1.0

        raw_scored: list[tuple[float, float]] = []
        res_scored: list[tuple[float, float]] = []
        for sym in MEGACAPS:
            sig = _signals(prices, sym, rebal, spy_mom)
            fwd = _fwd_return(prices, sym, rebal, end_ts)
            if sig is None or fwd is None:
                continue
            raw_scored.append((sig[0], fwd))
            res_scored.append((sig[1], fwd))
        if len(raw_scored) < 2 * TOP_N:
            continue
        months += 1
        raw_stats.append(_bucket(raw_scored))
        res_stats.append(_bucket(res_scored))

    def col(stats: list[tuple[float, float, float, float]], j: int) -> float:
        return sum(s[j] for s in stats) / len(stats)

    raw_ic, res_ic = col(raw_stats, 3), col(res_stats, 3)
    raw_tu = col(raw_stats, 0) - col(raw_stats, 1)
    res_tu = col(res_stats, 0) - col(res_stats, 1)
    bar1 = res_ic > raw_ic
    bar2 = res_tu > raw_tu
    adopt = bar1 and bar2
    verdict = (
        "ADOPT-CANDIDATE — residual momentum beats raw on BOTH rank-IC and top−universe "
        "spread. Next: swap the momentum leg in the composite and re-run the full "
        "walk-forward + PBO + crash gate before any capital."
        if adopt
        else "NO IMPROVEMENT — residual momentum does not beat raw on both pre-declared bars. "
        "Raw momentum stands; no further variants tried (that would be data-mining)."
    )

    pct = lambda x: f"{x * 100:+.2f}%"  # noqa: E731
    lines = [
        "# Residual vs raw momentum — selection-skill comparison",
        "",
        f"Pinned {args.prices.name} | {months} rebalances 2009→{last.date()} | "
        f"{LOOKBACK}d lookback, beta from daily regression | 21-bar forward, top/bottom {TOP_N}",
        "",
        "| signal | top-7 | universe | bottom-7 | top−universe | rank-IC |",
        "|---|---:|---:|---:|---:|---:|",
        f"| raw momentum | {pct(col(raw_stats, 0))} | {pct(col(raw_stats, 1))} | "
        f"{pct(col(raw_stats, 2))} | {pct(raw_tu)} | {raw_ic:+.3f} |",
        f"| residual momentum | {pct(col(res_stats, 0))} | {pct(col(res_stats, 1))} | "
        f"{pct(col(res_stats, 2))} | {pct(res_tu)} | {res_ic:+.3f} |",
        "",
        "(reference: AQR composite rank-IC +0.018, top−universe +0.96%/mo — see picking-skill.md)",
        "",
        "## Verdict (bars pre-declared in the module docstring)",
        "",
        verdict,
        "",
        f"- bar1 IC: residual {res_ic:+.3f} {'>' if bar1 else '<='} raw {raw_ic:+.3f}",
        f"- bar2 top−universe: residual {pct(res_tu)} {'>' if bar2 else '<='} raw {pct(raw_tu)}",
        "",
        "## Honest caveats",
        "- Megacap universe is small and high-beta-homogeneous; residualization has less to",
        "  strip here than in a broad universe — a weak result does NOT condemn residual",
        "  momentum generally, only its value ON THIS universe.",
        "- Survivorship-inflated levels; the IC and spread (relative) are the honest read.",
        "- Single pre-declared definition; the 126d lookback was NOT tuned.",
    ]
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
