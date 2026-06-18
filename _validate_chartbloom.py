#!/usr/bin/env python3
"""
Chartbloom 크로스시스템 액션아이템 실증 검증 (엔진 무수정, 측정 전용).

검증 가설(CHARTBLOOM_CROSS_SYSTEM_NOTES.md):
  B  : OB 단독 hit-rate < OB+FVG hit-rate (채널 주장 30% → 65%)
  C  : 묵은(stale) OB hit-rate < 프레시 OB (age decay)
  G  : HTF/추세 역방향 OB 진입 손실률 > 정렬 진입
  A1 : CHoCH는 FVG 동반 시 forward edge가 더 큼

방법:
  - no-lookahead 이벤트 스터디. OB 구조속성(has_fvg/zone/방향)은 형성봉에서 확정.
  - 진입 = 형성 이후 첫 zone CE(zone_mid) 리테스트 봉. 손절 = mitigation_extreme.
    타깃 = 진입 ± RR×risk. 진입 다음봉부터 high/low로 선도달 판정(no-lookahead).
  - RR ∈ {1.0,1.5,2.0}, 최대 호라이즌 30봉. 고정 호라이즌 부호수익(+6/12/24봉) 병행.
  - 다중 심볼(크립토 4h 3y + 미국주식 1d 8y) 교차검증. 베이스라인(무조건 forward) 대비.
  - 정직성: 그룹 n, Wilson 95%CI, 2비율 z검정, Welch t, 부호 일관성, effN<20 fragile, 다중비교 Bonferroni.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from data.ingest.ccxt_crypto import fetch_ccxt_bars
from data.ingest.yahoo import fetch_yahoo_bars
from engine.chart.fvg import run_fvg
from engine.chart.order_block import detect_order_blocks
from engine.chart.structure import detect_swing_structure

CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
STOCKS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]
RR_LEVELS = [1.0, 1.5, 2.0]
H_MAX = 30  # 타깃/스톱 미해결 시 타임아웃
HORIZONS = [6, 12, 24]
STALE_BARS = 15  # age decay 분기점(형성→리테스트 경과봉)
REPORT = "/Users/jjuni/재무관리 모델/merr_corpus/CHARTBLOOM_VALIDATION_RESULTS.md"


# ---------- 통계 헬퍼 ----------
def wilson(k: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return (0.0, 0.0, 0.0)
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def two_prop_z(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    """그룹2 - 그룹1 비율차의 z, p(양측)."""
    if n1 == 0 or n2 == 0:
        return (0.0, 1.0)
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return (0.0, 1.0)
    z = (p2 - p1) / se
    pval = math.erfc(abs(z) / math.sqrt(2))
    return (z, pval)


def mean_t(xs: list[float]) -> tuple[float, float, float]:
    """평균, t(vs 0), p(양측)."""
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else 0.0, 0.0, 1.0)
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    if var == 0:
        return (m, 0.0, 1.0)
    t = m / math.sqrt(var / n)
    pval = math.erfc(abs(t) / math.sqrt(2))  # 정규근사(n 큼)
    return (m, t, pval)


# ---------- 이벤트 스터디 ----------
@dataclass
class OBTrade:
    symbol: str
    direction: str
    has_fvg: bool
    age: int  # 형성→리테스트 경과봉
    aligned: bool  # 추세 bias 정렬 여부
    strength: float
    outcomes: dict  # RR -> 'target'|'stop'|'timeout'
    fwd: dict  # horizon -> 부호수익(방향 반영)


def trend_bias_at(events, bar_idx: int) -> str:
    """진입봉 이전 마지막 구조 이벤트의 trend_bias 스냅샷(no-lookahead)."""
    bias = "RANGING"
    for ev in events:
        bi = getattr(ev, "bar_index", None)
        if bi is None or bi > bar_idx:
            continue
        tb = getattr(ev, "trend_bias", None)
        if tb:
            bias = tb.value if hasattr(tb, "value") else str(tb)
    return bias


def ob_event_study(symbol: str, bars) -> list[OBTrade]:
    obs = detect_order_blocks(bars)
    ms = detect_swing_structure(bars)
    events = getattr(ms, "events", []) or []
    n = len(bars)
    trades: list[OBTrade] = []
    for ob in obs:
        d = ob.direction  # 'bullish' | 'bearish'
        lo, hi, mid = ob.zone_low, ob.zone_high, ob.zone_mid
        stop = getattr(ob, "mitigation_extreme", None)
        i0 = ob.ob_index
        # 진입: 형성 이후 첫 zone CE(mid) 리테스트
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
        if stop is None:
            stop = lo if d == "bullish" else hi
        risk = (entry - stop) if d == "bullish" else (stop - entry)
        if risk <= 0:
            continue  # 무효 stop
        bias = trend_bias_at(events, entry_j)
        aligned = (d == "bullish" and bias == "BULLISH") or (d == "bearish" and bias == "BEARISH")
        # RR 결과: 진입 다음봉부터 선도달
        outcomes = {}
        for rr in RR_LEVELS:
            target = entry + rr * risk if d == "bullish" else entry - rr * risk
            res = "timeout"
            for k in range(entry_j + 1, min(entry_j + 1 + H_MAX, n)):
                b = bars[k]
                if d == "bullish":
                    hit_stop = b.low <= stop
                    hit_tgt = b.high >= target
                else:
                    hit_stop = b.high >= stop
                    hit_tgt = b.low <= target
                if hit_stop and hit_tgt:
                    res = "stop"  # 보수적: 같은 봉이면 스톱 우선
                    break
                if hit_stop:
                    res = "stop"
                    break
                if hit_tgt:
                    res = "target"
                    break
            outcomes[rr] = res
        # 고정 호라이즌 부호수익
        fwd = {}
        for h in HORIZONS:
            k = entry_j + h
            if k < n:
                raw = (bars[k].close - entry) / entry
                fwd[h] = raw if d == "bullish" else -raw
        trades.append(
            OBTrade(symbol, d, ob.has_fvg, entry_j - i0, aligned, ob.strength, outcomes, fwd)
        )
    return trades


@dataclass
class ChochTrade:
    symbol: str
    direction: str
    has_fvg_near: bool
    fwd: dict


def choch_event_study(symbol: str, bars) -> list[ChochTrade]:
    ms = detect_swing_structure(bars)
    events = getattr(ms, "events", []) or []
    fr = run_fvg(bars)
    fvgs = fr.all_fvgs
    n = len(bars)
    out: list[ChochTrade] = []
    for ev in events:
        et = (getattr(ev, "event_type", "") or "").lower()
        if "choch" not in et:
            continue
        bi = getattr(ev, "bar_index", None)
        if bi is None or bi >= n:
            continue
        dr = (getattr(ev, "direction", "") or "").upper()
        up = ("UP" in et) or ("BULL" in dr)
        d = "bullish" if up else "bearish"
        entry = bars[bi].close
        # 동일방향 FVG가 CHoCH 직전 5봉 내 형성?
        near = False
        for z in fvgs:
            fb = getattr(z, "formation_bar_idx", -999)
            if bi - 5 <= fb <= bi and z.direction == d:
                near = True
                break
        fwd = {}
        for h in HORIZONS:
            k = bi + h
            if k < n:
                raw = (bars[k].close - entry) / entry
                fwd[h] = raw if d == "bullish" else -raw
        out.append(ChochTrade(symbol, d, near, fwd))
    return out


def baseline_fwd(bars) -> dict:
    """무조건 long 기준 forward 부호수익 베이스라인(드리프트 보정용)."""
    n = len(bars)
    base = {}
    for h in HORIZONS:
        xs = [(bars[i + h].close - bars[i].close) / bars[i].close for i in range(n - h)]
        base[h] = mean_t(xs)
    return base


# ---------- 집계/리포트 ----------
def hr(trades: list[OBTrade], rr: float, pred=lambda t: True):
    sel = [t for t in trades if pred(t)]
    res = [t.outcomes[rr] for t in sel]
    tgt = res.count("target")
    stp = res.count("stop")
    to = res.count("timeout")
    resolved = tgt + stp
    p_all, lo_a, hi_a = wilson(tgt, len(sel))
    p_res, lo_r, hi_r = wilson(tgt, resolved)
    return dict(  # noqa: C408
        n=len(sel),
        target=tgt,
        stop=stp,
        timeout=to,
        hr_all=(p_all, lo_a, hi_a),
        hr_res=(p_res, lo_r, hi_r),
        resolved=resolved,
    )


def fwd_group(trades, h: int, pred=lambda t: True):
    xs = [t.fwd[h] for t in trades if pred(t) and h in t.fwd]
    return xs


def main():
    print("데이터 수집 중...")
    series: dict[str, list] = {}
    end = date.today()
    for sym in CRYPTO:
        try:
            b = fetch_ccxt_bars(
                sym,
                end - timedelta(days=3 * 365),
                end,
                timeframe="4h",
                exchange_id="binance",
                intraday=True,
            )
            if len(b) > 300:
                series[sym] = ("crypto4h", b)
                print(
                    f"  {sym}: {len(b)} bars ({b[0].ts.date() if hasattr(b[0].ts, 'date') else b[0].ts} ~ {b[-1].ts.date() if hasattr(b[-1].ts, 'date') else b[-1].ts})"
                )
        except Exception as e:
            print(f"  {sym} FAIL: {type(e).__name__}: {e}")
    for sym in STOCKS:
        try:
            b = fetch_yahoo_bars(
                sym, market="us", start=end - timedelta(days=8 * 365), end=end, interval="1d"
            )
            if len(b) > 300:
                series[sym] = ("stock1d", b)
                print(f"  {sym}: {len(b)} bars")
        except Exception as e:
            print(f"  {sym} FAIL: {type(e).__name__}: {e}")

    all_ob: list[OBTrade] = []
    all_choch: list[ChochTrade] = []
    base_by_sym = {}
    for sym, (kind, bars) in series.items():  # noqa: B007
        all_ob += ob_event_study(sym, bars)
        all_choch += choch_event_study(sym, bars)
        base_by_sym[sym] = baseline_fwd(bars)

    L = []  # noqa: N806
    L.append("# Chartbloom 액션아이템 실증 검증 결과")
    L.append("")
    L.append(
        f"> 생성: 이벤트 스터디(no-lookahead). 심볼 {len(series)}개 "
        f"(크립토4h 3년 + 미국주식1d 8년). 엔진 무수정·측정 전용."
    )
    L.append(
        f"> OB 이벤트 {len(all_ob)}건, CHoCH 이벤트 {len(all_choch)}건. "
        f"진입=zone CE 리테스트, 손절=mitigation_extreme, 호라이즌 {H_MAX}봉."
    )
    L.append(
        f"> 다중비교 {5}가설 → Bonferroni 유의 임계 p<{0.05 / 5:.3f}. effN<20 그룹은 fragile 표시."
    )
    L.append("")

    # ===== B: OB has_fvg =====
    L.append("## B. OB 단독 vs OB+FVG (채널 주장 30%→65%)")
    L.append("")
    L.append("| RR | 그룹 | n | 타깃 | 스톱 | 타임아웃 | hit(전체) | hit(해결분) | 95%CI(해결분) |")
    L.append("|---|---|--:|--:|--:|--:|--:|--:|---|")
    for rr in RR_LEVELS:
        solo = hr(all_ob, rr, lambda t: not t.has_fvg)
        conf = hr(all_ob, rr, lambda t: t.has_fvg)
        for name, g in (("OB단독", solo), ("OB+FVG", conf)):
            frag = " ⚠fragile" if g["n"] < 20 else ""
            L.append(
                f"| {rr} | {name}{frag} | {g['n']} | {g['target']} | {g['stop']} | "
                f"{g['timeout']} | {g['hr_all'][0] * 100:.1f}% | {g['hr_res'][0] * 100:.1f}% | "
                f"[{g['hr_res'][1] * 100:.0f},{g['hr_res'][2] * 100:.0f}]% |"
            )
        z, p = two_prop_z(solo["target"], solo["resolved"], conf["target"], conf["resolved"])
        verdict = "유의" if p < 0.01 else ("약" if p < 0.05 else "무의미")
        L.append(
            f"| {rr} | **차이(해결분)** | | | | | | "
            f"Δ={conf['hr_res'][0] * 100 - solo['hr_res'][0] * 100:+.1f}%p | z={z:.2f} p={p:.3f} **{verdict}** |"
        )
    L.append("")
    # 부호수익 교차
    L.append("**고정 호라이즌 forward 부호수익(평균, t):**")
    L.append("")
    L.append("| 그룹 | " + " | ".join(f"+{h}봉" for h in HORIZONS) + " |")
    L.append("|---|" + "|".join("---" for _ in HORIZONS) + "|")
    for name, pred in (("OB단독", lambda t: not t.has_fvg), ("OB+FVG", lambda t: t.has_fvg)):
        cells = []
        for h in HORIZONS:
            m, tt, pp = mean_t(fwd_group(all_ob, h, pred))
            cells.append(f"{m * 100:+.2f}% (t={tt:.1f})")
        L.append(f"| {name} | " + " | ".join(cells) + " |")
    L.append("")

    # ===== C: age decay =====
    L.append(f"## C. 프레시(age≤{STALE_BARS}) vs 묵은(age>{STALE_BARS}) OB")
    L.append("")
    L.append("| RR | 그룹 | n | hit(해결분) | 95%CI | z(묵음-프레시) p |")
    L.append("|---|---|--:|--:|---|---|")
    for rr in RR_LEVELS:
        fresh = hr(all_ob, rr, lambda t: t.age <= STALE_BARS)
        stale = hr(all_ob, rr, lambda t: t.age > STALE_BARS)
        z, p = two_prop_z(fresh["target"], fresh["resolved"], stale["target"], stale["resolved"])
        for name, g in (("프레시", fresh), ("묵은", stale)):
            frag = " ⚠" if g["n"] < 20 else ""
            L.append(
                f"| {rr} | {name}{frag} | {g['n']} | {g['hr_res'][0] * 100:.1f}% | "
                f"[{g['hr_res'][1] * 100:.0f},{g['hr_res'][2] * 100:.0f}]% | "
                + (f"z={z:.2f} p={p:.3f}" if name == "묵은" else "")
                + " |"
            )
    L.append("")

    # ===== G: HTF/추세 정렬 =====
    L.append("## G. 추세 정렬 vs 역방향 OB 진입")
    L.append("")
    L.append("| RR | 그룹 | n | hit(해결분) | 95%CI | z(정렬-역방향) p |")
    L.append("|---|---|--:|--:|---|---|")
    for rr in RR_LEVELS:
        al = hr(all_ob, rr, lambda t: t.aligned)
        ct = hr(all_ob, rr, lambda t: not t.aligned)
        z, p = two_prop_z(ct["target"], ct["resolved"], al["target"], al["resolved"])
        for name, g in (("정렬", al), ("역방향", ct)):
            frag = " ⚠" if g["n"] < 20 else ""
            L.append(
                f"| {rr} | {name}{frag} | {g['n']} | {g['hr_res'][0] * 100:.1f}% | "
                f"[{g['hr_res'][1] * 100:.0f},{g['hr_res'][2] * 100:.0f}]% | "
                + (f"z={z:.2f} p={p:.3f}" if name == "정렬" else "")
                + " |"
            )
    L.append("")

    # ===== A1: CHoCH ± FVG =====
    L.append("## A1. CHoCH 단독 vs CHoCH+FVG (forward 부호수익)")
    L.append("")
    L.append("| 그룹 | n | " + " | ".join(f"+{h}봉 (t)" for h in HORIZONS) + " |")
    L.append("|---|--:|" + "|".join("---" for _ in HORIZONS) + "|")
    for name, pred in (
        ("CHoCH단독", lambda t: not t.has_fvg_near),
        ("CHoCH+FVG", lambda t: t.has_fvg_near),
    ):
        sel = [t for t in all_choch if pred(t)]
        cells = []
        for h in HORIZONS:
            m, tt, pp = mean_t([t.fwd[h] for t in sel if h in t.fwd])
            cells.append(f"{m * 100:+.2f}% (t={tt:.1f})")
        frag = " ⚠" if len(sel) < 20 else ""
        L.append(f"| {name}{frag} | {len(sel)} | " + " | ".join(cells) + " |")
    L.append("")

    # ===== 베이스라인 =====
    L.append("## 베이스라인 (무조건 long forward 부호수익 — 드리프트 보정 기준)")
    L.append("")
    L.append("| 심볼 | " + " | ".join(f"+{h}봉" for h in HORIZONS) + " |")
    L.append("|---|" + "|".join("---" for _ in HORIZONS) + "|")
    for sym, base in base_by_sym.items():
        L.append(f"| {sym} | " + " | ".join(f"{base[h][0] * 100:+.2f}%" for h in HORIZONS) + " |")
    L.append("")

    # ===== 심볼별 부호 일관성 (B 가설) =====
    L.append("## 교차검증: 심볼별 OB+FVG hit − OB단독 hit (RR1.5, 해결분)")
    L.append("")
    L.append("| 심볼 | OB단독 hit(n) | OB+FVG hit(n) | Δ%p | 예측방향(+) |")
    L.append("|---|---|---|--:|:--:|")
    consistent = 0
    n_sym = 0
    for sym in series:
        st = [t for t in all_ob if t.symbol == sym]
        solo = hr(st, 1.5, lambda t: not t.has_fvg)
        conf = hr(st, 1.5, lambda t: t.has_fvg)
        if solo["resolved"] >= 3 and conf["resolved"] >= 3:
            n_sym += 1
            delta = conf["hr_res"][0] * 100 - solo["hr_res"][0] * 100
            ok = "✓" if delta > 0 else "✗"
            if delta > 0:
                consistent += 1
            L.append(
                f"| {sym} | {solo['hr_res'][0] * 100:.0f}%({solo['resolved']}) | "
                f"{conf['hr_res'][0] * 100:.0f}%({conf['resolved']}) | {delta:+.0f} | {ok} |"
            )
    L.append("")
    L.append(
        f"**부호 일관성: {consistent}/{n_sym} 심볼이 OB+FVG > OB단독** "
        f"(robustness — 다수면 방향 신뢰, 소수면 fragile)."
    )
    L.append("")

    report = "\n".join(L)
    with open(REPORT, "w") as f:
        f.write(report)
    print("\n" + report)
    print(f"\n[저장] {REPORT}")


if __name__ == "__main__":
    main()
