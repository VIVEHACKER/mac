#!/usr/bin/env python3
"""
B 가설(OB 단독 vs OB+FVG) 공정 재검 v2 — v1 아티팩트 교정.

v1 결함 교정:
  1. tiny zone-RR → 거의 항상 타깃 선도달(포화). → **ATR(14) 기반 RR**로 교체.
  2. 리테스트된 OB만 집계(생존편향). → **setup-level 성공률** 추가:
     OB가 리테스트 전에 무효화(mitigation_extreme 반대 종가 돌파)되면 실패로 계상.
  3. n=71 과소. → 심볼·history 확대(크립토 5y 13종 + 주식 10y 8종).

엔진 무수정. no-lookahead. has_fvg로 그룹 비교 + 시장 간 부호 일관성.
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
    "MATIC/USDT",
    "ATOM/USDT",
]
STOCKS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
RR = 1.5
ATR_MULT_STOP = 1.0
H_MAX = 40
ATR_N = 14
REPORT = "/Users/jjuni/재무관리 모델/merr_corpus/CHARTBLOOM_VALIDATION_RESULTS.md"


def wilson(k, n):
    if n == 0:
        return (0.0, 0.0, 0.0)
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def two_prop_z(k1, n1, k2, n2):
    if n1 == 0 or n2 == 0:
        return (0.0, 1.0)
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return (0.0, 1.0)
    z = (p2 - p1) / se
    return (z, math.erfc(abs(z) / math.sqrt(2)))


def atr_at(bars, idx, n=ATR_N):
    """idx 시점까지의 ATR(no-lookahead)."""
    if idx < n:
        return None
    trs = []
    for k in range(idx - n + 1, idx + 1):
        h, low, pc = bars[k].high, bars[k].low, bars[k - 1].close
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    return sum(trs) / len(trs)


def ob_trades_v2(symbol, bars):
    """각 OB: (has_fvg, setup_outcome, entry_outcome|None).
    setup_outcome ∈ {'target','stop','invalidated','timeout','no_entry'}
    entry_outcome ∈ {'target','stop','timeout'} (리테스트 진입한 경우만)."""
    obs = detect_order_blocks(bars)
    n = len(bars)
    rows = []
    for ob in obs:
        d = ob.direction
        mid = ob.zone_mid
        stop_zone = getattr(ob, "mitigation_extreme", None)
        i0 = ob.ob_index
        atr = atr_at(bars, i0)
        if atr is None or atr <= 0:
            continue
        # ATR 기반 손절/타깃
        if d == "bullish":
            stop = mid - ATR_MULT_STOP * atr
            target = mid + RR * ATR_MULT_STOP * atr
            invalid_level = stop_zone if stop_zone is not None else stop
        else:
            stop = mid + ATR_MULT_STOP * atr
            target = mid - RR * ATR_MULT_STOP * atr
            invalid_level = stop_zone if stop_zone is not None else stop
        # 형성 이후 진행: 리테스트(zone CE) 전에 무효화되면 setup 실패
        entry_j = None
        invalidated = False
        for j in range(i0 + 1, n):
            b = bars[j]
            # 무효화: 방향 반대편으로 mitigation_extreme 종가 돌파(리테스트 전)
            if d == "bullish" and b.close < invalid_level:
                invalidated = True
                break
            if d == "bearish" and b.close > invalid_level:
                invalidated = True
                break
            # 리테스트(CE 도달)
            if d == "bullish" and b.low <= mid:
                entry_j = j
                break
            if d == "bearish" and b.high >= mid:
                entry_j = j
                break
        if invalidated:
            rows.append((ob.has_fvg, "invalidated", None))
            continue
        if entry_j is None:
            rows.append((ob.has_fvg, "no_entry", None))
            continue
        # 진입 후 ATR 타깃 vs 스톱
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
        rows.append((ob.has_fvg, res, res))
    return rows


def main():
    print("v2 데이터 수집...")
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
    print(f"  심볼 {len(series)}개 수집")

    allrows = []
    per_sym = {}
    for sym, bars in series.items():
        r = ob_trades_v2(sym, bars)
        allrows += r
        per_sym[sym] = r

    def entry_hr(rows, fvg):
        sel = [r for r in rows if r[0] == fvg and r[2] in ("target", "stop")]
        tgt = sum(1 for r in sel if r[2] == "target")
        return tgt, len(sel)

    def setup_rate(rows, fvg):
        # setup 성공 = target / (전체 OB - no_entry - timeout)  (invalidated/stop=실패, target=성공)
        sel = [r for r in rows if r[0] == fvg and r[1] in ("target", "stop", "invalidated")]
        tgt = sum(1 for r in sel if r[1] == "target")
        return tgt, len(sel)

    L = []
    L.append("")
    L.append("---")
    L.append("")
    L.append("## B 가설 공정 재검 v2 (ATR 스톱 + 생존편향 보정 + 심볼 확대)")
    L.append("")
    L.append(
        f"> `trader/_validate_chartbloom_v2.py`. 심볼 {len(series)}개(크립토5y+주식10y), "
        f"RR={RR} ATR×{ATR_MULT_STOP} 스톱, 호라이즌 {H_MAX}봉. OB 총 {len(allrows)}건."
    )
    L.append(
        "> v1 교정: ① zone-RR→ATR-RR(포화 제거) ② 리테스트 전 무효화 OB를 실패로 계상(생존편향 제거)."
    )
    L.append("")
    # entry-conditional
    L.append("### (a) 진입조건부 hit-rate (ATR 타깃 vs ATR 스톱)")
    L.append("")
    L.append("| 그룹 | n(해결) | 타깃 | hit | 95%CI |")
    L.append("|---|--:|--:|--:|---|")
    for name, fvg in (("OB단독", False), ("OB+FVG", True)):
        tgt, nn = entry_hr(allrows, fvg)
        p, lo, hi = wilson(tgt, nn)
        frag = " ⚠" if nn < 20 else ""
        L.append(
            f"| {name}{frag} | {nn} | {tgt} | {p * 100:.1f}% | [{lo * 100:.0f},{hi * 100:.0f}]% |"
        )
    t1, n1 = entry_hr(allrows, False)
    t2, n2 = entry_hr(allrows, True)
    z, pv = two_prop_z(t1, n1, t2, n2)
    L.append(
        f"| **차이** | | | Δ={(t2 / n2 if n2 else 0) * 100 - (t1 / n1 if n1 else 0) * 100:+.1f}%p | z={z:.2f} p={pv:.3f} **{'유의' if pv < 0.01 else ('약' if pv < 0.05 else '무의미')}** |"
    )
    L.append("")
    # setup-level (survivorship-corrected)
    L.append("### (b) Setup 성공률 (생존편향 보정: 무효화=실패 포함)")
    L.append("")
    L.append("| 그룹 | n(setup) | 성공 | 성공률 | 95%CI |")
    L.append("|---|--:|--:|--:|---|")
    for name, fvg in (("OB단독", False), ("OB+FVG", True)):
        tgt, nn = setup_rate(allrows, fvg)
        p, lo, hi = wilson(tgt, nn)
        frag = " ⚠" if nn < 20 else ""
        L.append(
            f"| {name}{frag} | {nn} | {tgt} | {p * 100:.1f}% | [{lo * 100:.0f},{hi * 100:.0f}]% |"
        )
    s1, m1 = setup_rate(allrows, False)
    s2, m2 = setup_rate(allrows, True)
    z2, pv2 = two_prop_z(s1, m1, s2, m2)
    L.append(
        f"| **차이** | | | Δ={(s2 / m2 if m2 else 0) * 100 - (s1 / m1 if m1 else 0) * 100:+.1f}%p | z={z2:.2f} p={pv2:.3f} **{'유의' if pv2 < 0.01 else ('약' if pv2 < 0.05 else '무의미')}** |"
    )
    L.append("")
    # 분포
    from collections import Counter

    dist = Counter(r[1] for r in allrows)
    L.append(
        f"**OB setup 분포**: {dict(dist)} (no_entry=리테스트無, invalidated=리테스트전 무효화)."
    )
    L.append("")
    # 교차검증
    L.append("### (c) 심볼별 부호 일관성 (setup 성공률, OB+FVG − OB단독)")
    L.append("")
    cons = 0
    nsym = 0
    detail = []
    for sym, rows in per_sym.items():
        s1, m1 = setup_rate(rows, False)
        s2, m2 = setup_rate(rows, True)
        if m1 >= 5 and m2 >= 5:
            nsym += 1
            delta = (s2 / m2 - s1 / m1) * 100
            if delta > 0:
                cons += 1
            detail.append(f"{sym} {delta:+.0f}")
    L.append(f"부호 일관성: **{cons}/{nsym}** 심볼이 OB+FVG>OB단독. ({', '.join(detail)})")
    L.append("")
    L.append(
        f"*판정: 진입 hit Δ p={pv:.3f} / setup Δ p={pv2:.3f} / 교차 {cons}/{nsym}. "
        f"세 지표 모두 유의+다수 일관이어야 B 채택. 아니면 기존 판정(미확정) 유지.*"
    )

    block = "\n".join(L)
    with open(REPORT, "a") as f:
        f.write("\n" + block)
    print(block)
    print(f"\n[append] {REPORT}")


if __name__ == "__main__":
    main()
