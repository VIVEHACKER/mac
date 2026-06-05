"""Walk-forward forward-return validation of the chart-reading entry signals.

Honest edge test (NOT a guarantee of profit): at each bar t we run read_chart over the
trailing WINDOW bars (causal, no lookahead) and record the decision + confluence score,
then measure the realized forward return over several horizons. We then ask:

  - Do ENTER_NOW / SCALE_IN bars have higher forward returns than AVOID bars?
  - Is the bucket ordering monotone (ENTER > SCALE > WAIT > AVOID)?
  - Does the confluence score rank-correlate (IC) with forward return?
  - Is the ENTER-minus-AVOID spread distinguishable from zero under a moving-block
    bootstrap (which respects the autocorrelation of overlapping windows)?

Caveats this script makes explicit, not hides:
  - Only OHLCV-derivable signals are tested (no order book / open interest — no history).
  - direction is fixed long; ENTER requires BULLISH structure, which is itself momentum,
    so any edge is partly trend-following, not pure "chart reading skill".
  - Overlapping windows → naive t-tests overstate significance; we use a block bootstrap.

Usage: python -m scripts.chart_signal_validation [--crypto BTC/USDT,ETH/USDT ...]
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from data.ingest.ccxt_crypto import fetch_ccxt_bars
from data.ingest.yahoo import fetch_yahoo_bars
from data.models import PriceBar
from engine.chart.read import read_chart

HORIZONS = (3, 5, 10, 20)
WINDOW = 250
ACT = {"ENTER_NOW", "SCALE_IN"}
BUCKET_ORDER = ["ENTER_NOW", "SCALE_IN", "WAIT_FOR_PULLBACK", "AVOID"]


@dataclass
class Obs:
    symbol: str
    decision: str
    confluence: float
    fwd: dict[int, float]


def walk_forward(symbol: str, bars: list[PriceBar], direction: str = "long") -> list[Obs]:
    out: list[Obs] = []
    max_h = max(HORIZONS)
    for i in range(WINDOW - 1, len(bars) - max_h):
        window = bars[i - WINDOW + 1 : i + 1]
        entry = bars[i].close
        if entry <= 0:
            continue
        try:
            read = read_chart(window, direction=direction)
        except Exception:  # noqa: BLE001 - a detector failure should skip the bar, not abort
            continue
        fwd = {h: bars[i + h].close / entry - 1.0 for h in HORIZONS}
        out.append(Obs(symbol, read.decision.value, read.confluence, fwd))
    return out


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda k: values[k])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3:
        return 0.0
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den > 0 else 0.0


def block_bootstrap_spread(
    act_flags: list[bool],
    avoid_flags: list[bool],
    rets: list[float],
    *,
    n_boot: int = 2000,
    block: int = 20,
    seed: int = 7,
) -> tuple[float | None, float | None, float | None]:
    """Moving-block bootstrap CI for mean(ret|ACT) - mean(ret|AVOID), respecting
    serial correlation of overlapping windows."""
    n = len(rets)
    if n < block * 3:
        return None, None, None
    rnd = random.Random(seed)

    def spread(idx: list[int]) -> float | None:
        a = [rets[k] for k in idx if act_flags[k]]
        v = [rets[k] for k in idx if avoid_flags[k]]
        if not a or not v:
            return None
        return sum(a) / len(a) - sum(v) / len(v)

    base = spread(list(range(n)))
    n_blocks = max(1, n // block)
    samples: list[float] = []
    for _ in range(n_boot):
        idx: list[int] = []
        for _b in range(n_blocks):
            start = rnd.randint(0, n - block)
            idx.extend(range(start, start + block))
        s = spread(idx)
        if s is not None:
            samples.append(s)
    if not samples:
        return base, None, None
    samples.sort()
    lo = samples[int(0.05 * len(samples))]
    hi = samples[min(len(samples) - 1, int(0.95 * len(samples)))]
    return base, lo, hi


def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def summarize(obs: list[Obs]) -> str:
    lines: list[str] = []
    n_total = len(obs)
    lines.append(f"총 평가 시점: {n_total}")
    if not n_total:
        return "\n".join(lines)

    # bucket counts
    by_bucket: dict[str, list[Obs]] = defaultdict(list)
    for o in obs:
        by_bucket[o.decision].append(o)
    lines.append("")
    lines.append("## 결정 버킷 분포")
    for b in BUCKET_ORDER:
        k = len(by_bucket.get(b, []))
        lines.append(f"  {b:<18} {k:>6}  ({k / n_total * 100:5.1f}%)")

    # per-horizon stats
    for h in HORIZONS:
        rets_all = [o.fwd[h] for o in obs]
        lines.append("")
        lines.append(f"## Horizon +{h} bars")
        lines.append(
            f"  baseline (전 시점 평균): mean {_fmt_pct(statistics.mean(rets_all))} "
            f"| hit {sum(r > 0 for r in rets_all) / len(rets_all) * 100:.1f}%"
        )
        lines.append("  | bucket | n | mean fwd | median | hit% |")
        lines.append("  |---|---:|---:|---:|---:|")
        for b in BUCKET_ORDER:
            grp = by_bucket.get(b, [])
            if not grp:
                lines.append(f"  | {b} | 0 | n/a | n/a | n/a |")
                continue
            rs = [o.fwd[h] for o in grp]
            lines.append(
                f"  | {b} | {len(rs)} | {_fmt_pct(statistics.mean(rs))} | "
                f"{_fmt_pct(statistics.median(rs))} | {sum(r > 0 for r in rs) / len(rs) * 100:.1f}% |"
            )
        # ACT vs AVOID spread + bootstrap
        act_flags = [o.decision in ACT for o in obs]
        avoid_flags = [o.decision == "AVOID" for o in obs]
        base, lo, hi = block_bootstrap_spread(act_flags, avoid_flags, rets_all)
        sig = (
            "유의(CI가 0 미포함)"
            if (lo is not None and (lo > 0 or hi < 0))
            else "불명확(CI가 0 포함)"
        )
        lines.append(
            f"  ACT(ENTER+SCALE) − AVOID 스프레드: {_fmt_pct(base)} "
            f"[90% 블록부트 CI {_fmt_pct(lo)} ~ {_fmt_pct(hi)}] → {sig}"
        )
        # IC: confluence vs fwd
        ic = spearman([o.confluence for o in obs], rets_all)
        lines.append(f"  IC(컨플루언스 vs fwd, Spearman): {ic:+.3f}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crypto", default="BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT")
    parser.add_argument("--crypto-tf", default="4h")
    parser.add_argument("--crypto-days", type=int, default=160)
    parser.add_argument("--stocks", default="SPY,AAPL,MSFT,NVDA")
    parser.add_argument("--stock-years", type=int, default=6)
    parser.add_argument("--exchange", default="binance")
    args = parser.parse_args()

    end = date.today()
    all_obs: list[Obs] = []
    sym_lines: list[str] = []

    for sym in [s.strip() for s in args.crypto.split(",") if s.strip()]:
        try:
            bars = fetch_ccxt_bars(
                sym,
                end - timedelta(days=args.crypto_days),
                end,
                timeframe=args.crypto_tf,
                exchange_id=args.exchange,
                intraday=True,
            )
        except Exception as exc:  # noqa: BLE001
            sym_lines.append(f"  {sym} ({args.crypto_tf}): FETCH FAIL — {exc}")
            continue
        obs = walk_forward(sym, bars)
        all_obs.extend(obs)
        sym_lines.append(f"  {sym} ({args.crypto_tf}): {len(bars)} bars → {len(obs)} eval points")

    for sym in [s.strip() for s in args.stocks.split(",") if s.strip()]:
        try:
            bars = fetch_yahoo_bars(sym, "us", end - timedelta(days=args.stock_years * 365), end)
        except Exception as exc:  # noqa: BLE001
            sym_lines.append(f"  {sym} (1d): FETCH FAIL — {exc}")
            continue
        obs = walk_forward(sym, bars)
        all_obs.extend(obs)
        sym_lines.append(f"  {sym} (1d): {len(bars)} bars → {len(obs)} eval points")

    print("=" * 70)
    print(" 차트 신호 Forward-Return 검증 (walk-forward, no-lookahead)")
    print("=" * 70)
    print("심볼별 표본:")
    print("\n".join(sym_lines))
    print()
    print("주의: OHLCV 신호만(호가/OI 제외) · direction=long(모멘텀 교란 존재) ·")
    print("      ACT는 BULLISH 구조 필요 → 엣지 일부는 추세추종일 수 있음.")
    print()
    print("--- 크립토만 ---")
    print(summarize([o for o in all_obs if "/" in o.symbol]))
    print()
    print("--- 주식만 ---")
    print(summarize([o for o in all_obs if "/" not in o.symbol]))
    print()
    print("--- 전체 통합 ---")
    print(summarize(all_obs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
