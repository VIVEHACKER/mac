"""Edge search: can conditioning the chart ENTER signal on DISCOUNT (pullback) recover
a positive forward-return edge?

Motivation (from docs/CHART_VALIDATION.md): the raw ENTER_NOW/SCALE_IN signal has a
*negative* IC — it buys local strength that mean-reverts. This script tests ONE
mechanism-based hypothesis (not a 50-parameter fit):

  H: ACT signals fired in the DISCOUNT half of the recent dealing range (a pullback)
     have a *positive* forward edge, while ACT in PREMIUM (chasing highs) are negative.

For each ACT bar we record range_pos = (close - low_N) / (high_N - low_N) over a trailing
window, then bucket ACT signals by discount/mid/premium and measure forward returns, hit
rate, the discount-minus-premium spread (moving-block bootstrap), and IC(range_pos, fwd).
A robust result must hold across BOTH markets and multiple horizons to count.

Observations are cached to out/chart_edge_obs_<tag>.jsonl so the (expensive) read_chart
walk-forward runs once and analysis can be re-run cheaply.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from datetime import date, timedelta

from data.ingest.ccxt_crypto import fetch_ccxt_bars
from data.ingest.yahoo import fetch_yahoo_bars
from data.models import PriceBar
from engine.chart.read import read_chart

HORIZONS = (5, 10, 20)
WINDOW = 250
RANGE_N = 50
ACT = {"ENTER_NOW", "SCALE_IN"}
OUT_DIR = "out"


def _range_pos(window: list[PriceBar], n: int) -> float:
    seg = window[-n:] if len(window) >= n else window
    hi = max(b.high for b in seg)
    lo = min(b.low for b in seg)
    close = window[-1].close
    return (close - lo) / (hi - lo) if hi > lo else 0.5


def collect(symbol: str, bars: list[PriceBar], market: str) -> list[dict]:
    out: list[dict] = []
    max_h = max(HORIZONS)
    for i in range(WINDOW - 1, len(bars) - max_h):
        window = bars[i - WINDOW + 1 : i + 1]
        entry = bars[i].close
        if entry <= 0:
            continue
        try:
            read = read_chart(window, direction="long")
        except Exception:  # noqa: BLE001
            continue
        out.append(
            {
                "symbol": symbol,
                "market": market,
                "decision": read.decision.value,
                "confluence": read.confluence,
                "trend_bias": read.trend_bias.value,
                "range_pos": _range_pos(window, RANGE_N),
                "fwd": {str(h): bars[i + h].close / entry - 1.0 for h in HORIZONS},
            }
        )
    return out


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def _spearman(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3:
        return 0.0

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda k: v[k])
        rk = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                rk[order[k]] = (i + j) / 2.0
            i = j + 1
        return rk

    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den > 0 else 0.0


def _block_boot(a_rets: list[float], b_rets: list[float], *, n_boot: int = 2000, seed: int = 11):
    """Bootstrap CI for mean(a) - mean(b) (independent resamples; groups are sub-sequences)."""
    if len(a_rets) < 15 or len(b_rets) < 15:
        return None, None
    rnd = random.Random(seed)
    samples = []
    for _ in range(n_boot):
        a = [a_rets[rnd.randrange(len(a_rets))] for _ in range(len(a_rets))]
        b = [b_rets[rnd.randrange(len(b_rets))] for _ in range(len(b_rets))]
        samples.append(sum(a) / len(a) - sum(b) / len(b))
    samples.sort()
    return samples[int(0.05 * len(samples))], samples[int(0.95 * len(samples))]


def analyze(obs: list[dict], label: str) -> None:
    act = [o for o in obs if o["decision"] in ACT]
    print(f"\n========== {label} ==========")
    print(f"전체 {len(obs)} · ACT {len(act)}")
    if len(act) < 30:
        print("ACT 표본 부족")
        return
    disc = [o for o in act if o["range_pos"] < 0.4]
    mid = [o for o in act if 0.4 <= o["range_pos"] <= 0.6]
    prem = [o for o in act if o["range_pos"] > 0.6]
    print(f"ACT 구간: 디스카운트<0.4 {len(disc)} · 중립 {len(mid)} · 프리미엄>0.6 {len(prem)}")
    for h in HORIZONS:
        hk = str(h)
        dr = [o["fwd"][hk] for o in disc]
        pr = [o["fwd"][hk] for o in prem]
        ar = [o["fwd"][hk] for o in act]
        rng = [o["range_pos"] for o in act]
        ic = _spearman(rng, ar)  # >0 means higher range_pos (premium) -> higher fwd
        lo, hi = _block_boot(dr, pr)
        sig = "유의" if (lo is not None and (lo > 0 or hi < 0)) else "불명확"
        print(
            f"  +{h}봉 | 디스카운트 {_fmt(_mean(dr))} "
            f"(적중 {sum(r > 0 for r in dr) / len(dr) * 100:.0f}%) "
            f"| 프리미엄 {_fmt(_mean(pr))} "
            f"(적중 {sum(r > 0 for r in pr) / len(pr) * 100:.0f}%) "
            f"| 디−프 {_fmt((_mean(dr) or 0) - (_mean(pr) or 0))} "
            f"[CI {_fmt(lo)}~{_fmt(hi)} {sig}] "
            f"| IC(range_pos→fwd) {ic:+.3f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crypto", default="BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT")
    parser.add_argument("--crypto-tf", default="4h")
    parser.add_argument("--crypto-days", type=int, default=160)
    parser.add_argument("--stocks", default="")
    parser.add_argument("--stock-years", type=int, default=8)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--tag", default="crypto")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    cache = os.path.join(OUT_DIR, f"chart_edge_obs_{args.tag}.jsonl")
    obs: list[dict] = []
    if os.path.exists(cache):
        with open(cache) as fh:
            obs = [json.loads(line) for line in fh if line.strip()]
        print(f"[cache] {cache}: {len(obs)} obs")
    else:
        end = date.today()
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
                print(f"  {sym}: FETCH FAIL {exc}")
                continue
            o = collect(sym, bars, "crypto")
            obs.extend(o)
            print(f"  {sym} ({args.crypto_tf}): {len(bars)} bars → {len(o)} obs")
        for sym in [s.strip() for s in args.stocks.split(",") if s.strip()]:
            try:
                bars = fetch_yahoo_bars(
                    sym, "us", end - timedelta(days=args.stock_years * 365), end
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  {sym}: FETCH FAIL {exc}")
                continue
            o = collect(sym, bars, "stock")
            obs.extend(o)
            print(f"  {sym} (1d): {len(bars)} bars → {len(o)} obs")
        with open(cache, "w") as fh:
            for o in obs:
                fh.write(json.dumps(o) + "\n")
        print(f"[saved] {cache}: {len(obs)} obs")

    print("\n가설: ACT 신호를 디스카운트(되돌림) 구간으로 조건화하면 엣지가 양으로 뒤집히는가?")
    analyze([o for o in obs if o["market"] == "crypto"], "크립토 ACT")
    analyze([o for o in obs if o["market"] == "stock"], "주식 ACT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
