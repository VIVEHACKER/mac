#!/usr/bin/env python3
"""
B 가설 v3 — drift-neutral 재검. v2의 강세장 포화(92~97%) 교란 제거.

핵심: 절대 hit/return이 아니라 **드리프트 대비 초과**로 OB+FVG vs OB단독을 비교.
  1. 초과수익 = OB진입 forward(방향부호) − 심볼 베이스라인 드리프트(같은 호라이즌).
     → 강세장 공통 드리프트 상쇄. FVG가 진짜 알파면 초과수익 그룹차가 유의해야.
  2. 고RR(3×ATR 타깃 / 1×ATR 스톱) → hit-rate 비포화.
  3. 롱/숏 분리 — 강세장은 롱만 띄움. FVG 효과가 실재면 숏(bearish OB)에서도 초과수익↑.
엔진 무수정. no-lookahead. Welch t로 그룹 초과수익 차이 검정.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from data.ingest.ccxt_crypto import fetch_ccxt_bars
from data.ingest.yahoo import fetch_yahoo_bars
from engine.chart.order_block import detect_order_blocks

CRYPTO = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "DOGE/USDT",
    "LINK/USDT",
    "AVAX/USDT",
    "LTC/USDT",
    "DOT/USDT",
    "ATOM/USDT",
]
STOCKS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
RR = 3.0
ATR_MULT_STOP = 1.0
H_MAX = 60
HORIZONS = [6, 12, 24]
ATR_N = 14
REPORT = "/Users/jjuni/재무관리 모델/merr_corpus/CHARTBLOOM_VALIDATION_RESULTS.md"


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def var(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def welch(a, b):
    """그룹b - 그룹a 평균차 t, p(양측, 정규근사)."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return (0.0, 1.0)
    se = math.sqrt(var(a) / na + var(b) / nb)
    if se == 0:
        return (0.0, 1.0)
    t = (mean(b) - mean(a)) / se
    return (t, math.erfc(abs(t) / math.sqrt(2)))


def one_sample_t(xs):
    n = len(xs)
    if n < 2:
        return (mean(xs), 0.0, 1.0)
    m, v = mean(xs), var(xs)
    if v == 0:
        return (m, 0.0, 1.0)
    t = m / math.sqrt(v / n)
    return (m, t, math.erfc(abs(t) / math.sqrt(2)))


def atr_at(bars, idx, n=ATR_N):
    if idx < n:
        return None
    trs = []
    for k in range(idx - n + 1, idx + 1):
        h, low, pc = bars[k].high, bars[k].low, bars[k - 1].close
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    return sum(trs) / len(trs)


def baseline_long(bars, h):
    """무조건 long 평균 forward(드리프트)."""
    n = len(bars)
    xs = [(bars[i + h].close - bars[i].close) / bars[i].close for i in range(n - h)]
    return mean(xs)


def ob_rows_v3(symbol, bars):
    """각 OB 진입: dict(has_fvg, dir, excess{h}, hi_rr_outcome)."""
    obs = detect_order_blocks(bars)
    n = len(bars)
    base = {h: baseline_long(bars, h) for h in HORIZONS}
    rows = []
    for ob in obs:
        d = ob.direction
        mid = ob.zone_mid
        i0 = ob.ob_index
        atr = atr_at(bars, i0)
        if atr is None or atr <= 0:
            continue
        # 진입: 첫 CE 리테스트
        entry_j = None
        for j in range(i0 + 1, n):
            b = bars[j]
            if d == "bullish" and b.low <= mid:
                entry_j = j
                break
            if d == "bearish" and b.high >= mid:
                entry_j = j
                break
        if entry_j is None:
            continue
        entry = mid
        # 초과수익(드리프트 차감)
        excess = {}
        for h in HORIZONS:
            k = entry_j + h
            if k < n:
                raw = (bars[k].close - entry) / entry
                signed = raw if d == "bullish" else -raw
                drift = base[h] if d == "bullish" else -base[h]
                excess[h] = signed - drift
        # 고RR hit (3×ATR 타깃 vs 1×ATR 스톱)
        if d == "bullish":
            stop, target = mid - ATR_MULT_STOP * atr, mid + RR * ATR_MULT_STOP * atr
        else:
            stop, target = mid + ATR_MULT_STOP * atr, mid - RR * ATR_MULT_STOP * atr
        res = "timeout"
        for k in range(entry_j + 1, min(entry_j + 1 + H_MAX, n)):
            b = bars[k]
            if d == "bullish":
                hs, ht = b.low <= stop, b.high >= target
            else:
                hs, ht = b.high >= stop, b.low <= target
            if hs:
                res = "stop"
                break
            if ht:
                res = "target"
                break
        rows.append(dict(has_fvg=ob.has_fvg, dir=d, excess=excess, hi=res))  # noqa: C408
    return rows


def main():
    print("v3 데이터 수집...")
    series = {}
    end = date.today()
    for sym in CRYPTO:
        try:
            b = fetch_ccxt_bars(
                sym,
                end - timedelta(days=5 * 365),
                end,
                timeframe="4h",
                exchange_id="binance",
                intraday=True,
            )
            if len(b) > 300:
                series[sym] = b
        except Exception as e:
            print(f"  {sym} skip ({type(e).__name__})")
    for sym in STOCKS:
        try:
            b = fetch_yahoo_bars(
                sym, market="us", start=end - timedelta(days=10 * 365), end=end, interval="1d"
            )
            if len(b) > 300:
                series[sym] = b
        except Exception as e:
            print(f"  {sym} skip ({type(e).__name__})")
    print(f"  심볼 {len(series)}개")

    allrows = []
    for sym, bars in series.items():
        allrows += ob_rows_v3(sym, bars)

    def exc(rows, h, fvg=None, dr=None):
        return [
            r["excess"][h]
            for r in rows
            if h in r["excess"]
            and (fvg is None or r["has_fvg"] == fvg)
            and (dr is None or r["dir"] == dr)
        ]

    L = []  # noqa: N806
    L.append("")
    L.append("---")
    L.append("")
    L.append("## B 가설 v3 — drift-neutral (초과수익 + 고RR + 롱숏분리)")
    L.append("")
    L.append(
        f"> `trader/_validate_chartbloom_v3.py`. 심볼 {len(series)}개, OB 진입 {len(allrows)}건. "
        f"초과수익=forward−심볼드리프트, 고RR={RR}×ATR."
    )
    L.append("")
    # (a) 초과수익: OB+FVG vs OB단독 (전체)
    L.append("### (a) 초과수익(드리프트 차감) 평균 — OB단독 vs OB+FVG")
    L.append("")
    L.append("| 호라이즌 | OB단독 (n, 평균, t vs0) | OB+FVG (n, 평균, t vs0) | 그룹차 Welch t, p |")
    L.append("|---|---|---|---|")
    for h in HORIZONS:
        solo = exc(allrows, h, fvg=False)
        conf = exc(allrows, h, fvg=True)
        ms, ts, ps = one_sample_t(solo)
        mc, tc, pc = one_sample_t(conf)
        t, p = welch(solo, conf)
        v = "유의" if p < 0.01 else ("약" if p < 0.05 else "무의미")
        L.append(
            f"| +{h}봉 | {len(solo)}, {ms * 100:+.2f}%, t={ts:.1f} | "
            f"{len(conf)}, {mc * 100:+.2f}%, t={tc:.1f} | t={t:.2f} p={p:.3f} **{v}** |"
        )
    L.append("")
    # (b) 롱숏 분리 (+12봉 기준)
    L.append("### (b) 롱/숏 분리 초과수익 (+12봉) — 드리프트 방향 통제")
    L.append("")
    L.append("| 방향 | OB단독 (n, 초과) | OB+FVG (n, 초과) | 그룹차 p |")
    L.append("|---|---|---|---|")
    for dr, lbl in (("bullish", "롱(demand)"), ("bearish", "숏(supply)")):
        solo = exc(allrows, 12, fvg=False, dr=dr)
        conf = exc(allrows, 12, fvg=True, dr=dr)
        t, p = welch(solo, conf)
        L.append(
            f"| {lbl} | {len(solo)}, {mean(solo) * 100:+.2f}% | "
            f"{len(conf)}, {mean(conf) * 100:+.2f}% | p={p:.3f} |"
        )
    L.append("")
    # (c) 고RR hit (비포화)
    L.append(f"### (c) 고RR hit-rate ({RR}×ATR 타깃 / 1×ATR 스톱 — 비포화)")
    L.append("")
    L.append("| 그룹 | n(해결) | target | hit |")
    L.append("|---|--:|--:|--:|")
    for name, fvg in (("OB단독", False), ("OB+FVG", True)):
        sel = [r for r in allrows if r["has_fvg"] == fvg and r["hi"] in ("target", "stop")]
        tgt = sum(1 for r in sel if r["hi"] == "target")
        hrp = tgt / len(sel) * 100 if sel else 0
        L.append(f"| {name} | {len(sel)} | {tgt} | {hrp:.1f}% |")
    sa = [r for r in allrows if not r["has_fvg"] and r["hi"] in ("target", "stop")]
    sc = [r for r in allrows if r["has_fvg"] and r["hi"] in ("target", "stop")]
    ta = sum(1 for r in sa if r["hi"] == "target")
    tc = sum(1 for r in sc if r["hi"] == "target")
    from math import sqrt

    if sa and sc:
        p1, p2 = ta / len(sa), tc / len(sc)
        pp = (ta + tc) / (len(sa) + len(sc))
        se = sqrt(pp * (1 - pp) * (1 / len(sa) + 1 / len(sc)))
        zz = (p2 - p1) / se if se else 0
        pv = math.erfc(abs(zz) / sqrt(2))
        L.append(
            f"| **차이** | | Δ={(p2 - p1) * 100:+.1f}%p | z={zz:.2f} p={pv:.3f} "
            f"**{'유의' if pv < 0.01 else ('약' if pv < 0.05 else '무의미')}** |"
        )
    L.append("")
    L.append(
        "*v3 판정: 초과수익 그룹차·고RR hit 그룹차가 모두 무의미하면 B 최종 미확정(drift 제거 후에도 FVG 무효과). "
        "롱·숏 양방향 초과수익이 유의+양수여야 진짜 FVG 알파.*"
    )

    block = "\n".join(L)
    with open(REPORT, "a") as f:
        f.write("\n" + block)
    print(block)
    print(f"\n[append] {REPORT}")


if __name__ == "__main__":
    main()
