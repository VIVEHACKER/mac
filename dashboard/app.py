"""재무관리 모델 — 통합 트레이딩 대시보드 (Streamlit, 로컬 전용).

직접 만든 기능 전부를 한 화면에서: 종목선정(AQR/모멘텀) · 차트리딩(SMC/ICT) ·
추천기(evaluate_ticker) · 검증결과(chartbloom/백테스트) · 예측(금리/CPI) ·
페이퍼원장(forward-OOS) · RAG(경제분석 챗봇).

실행:  cd "…/trader" && .venv/bin/streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from data.catalog import DEFAULT_CATALOG_PATH, MarketDataCatalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / DEFAULT_CATALOG_PATH
OUT_DIR = ROOT / "out"
MERR = Path("/Users/jjuni/재무관리 모델/merr_corpus")
RAG_URL = "http://localhost:8800"

# ─────────────────────────────────────────────────────────────────────────────
# UI / 테마
# ─────────────────────────────────────────────────────────────────────────────
_CSS = """
<style>
  .block-container {padding-top: 1.2rem; max-width: 1500px;}
  h1, h2, h3 {letter-spacing: -0.01em;}
  [data-testid="stMetricValue"] {font-variant-numeric: tabular-nums;}
  .stDataFrame {font-variant-numeric: tabular-nums;}
  .badge {display:inline-block;padding:3px 12px;border-radius:6px;font-weight:600;font-size:0.9rem;}
  .badge-buy {background:rgba(38,166,154,.18);color:#26a69a;border:1px solid #26a69a;}
  .badge-hold {background:rgba(255,202,40,.15);color:#ffca28;border:1px solid #ffca28;}
  .badge-avoid {background:rgba(239,83,80,.15);color:#ef5350;border:1px solid #ef5350;}
  .hdr-sub {color:#8a93b8;font-size:0.92rem;margin-top:-0.4rem;}
  div[data-baseweb="tab-list"] {gap: 2px;}
  button[data-baseweb="tab"] {font-weight:600;}
</style>
"""


def _badge(action: str) -> str:
    a = (action or "").upper()
    cls = (
        "badge-buy"
        if a in ("BUY", "ENTER_NOW", "SCALE_IN")
        else ("badge-avoid" if a in ("AVOID", "SELL") else "badge-hold")
    )
    return f'<span class="badge {cls}">{a or "—"}</span>'


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 로더
# ─────────────────────────────────────────────────────────────────────────────
def _live_fetch(symbol: str, market: str, tf: str = "1d", days: int = 420):
    """카탈로그에 없을 때 라이브 페치(crypto=ccxt, us=yahoo, kospi/kosdaq=pykrx)."""
    end = datetime.now(tz=UTC).date()
    start = end - timedelta(days=days)
    if market == "crypto":
        from data.ingest.ccxt_crypto import fetch_ccxt_bars

        return fetch_ccxt_bars(
            symbol, start, end, timeframe=tf, exchange_id="binance", intraday=(tf != "1d")
        )
    if market == "us":
        from data.ingest.yahoo import fetch_yahoo_bars

        return fetch_yahoo_bars(symbol, market="us", start=start, end=end, interval="1d")
    if market in ("kospi", "kosdaq"):
        from data.ingest.pykrx_kr import fetch_pykrx_bars

        return fetch_pykrx_bars(symbol, market=market, start=start, end=end)
    return []


def _load_universe(catalog, symbols, market, live=False, tf="1d"):
    bars_by_symbol = {}
    for s in symbols:
        b = catalog.get_bars(s, market=market)
        if not b and live:
            try:
                b = _live_fetch(s, market, tf)
            except Exception:
                b = []
        if b:
            bars_by_symbol[s] = b
    return bars_by_symbol


def _load_fundamentals(catalog, symbols, market):
    out = {}
    asof = datetime.now(tz=UTC).replace(tzinfo=None)
    for s in symbols:
        try:
            rows = catalog.get_fundamentals(s, market=market, as_of=asof)
            if rows:
                out[s] = rows[0]
        except Exception:
            pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 탭 1 — 종목선정 (AQR 팩터 + 모멘텀)
# ─────────────────────────────────────────────────────────────────────────────
def _render_screener(catalog) -> None:
    st.subheader("종목 선정 — AQR 팩터 · 모멘텀 랭크")
    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    with c1:
        uni = st.text_input(
            "유니버스 (쉼표 구분)", "MSFT,AAPL,NVDA,AMZN,META,GOOGL,AVGO,TSLA", key="scr_uni"
        )
    with c2:
        market = st.selectbox("시장", ["us", "kospi", "kosdaq", "crypto"], key="scr_mkt")
    with c3:
        lookback = st.number_input("Lookback", 20, 504, 126, key="scr_lb")
    with c4:
        live = st.checkbox(
            "라이브 페치",
            value=True,
            key="scr_live",
            help="카탈로그에 없으면 yahoo/ccxt/pykrx로 즉시 수집",
        )
    if not st.button("스크리닝 실행", type="primary", key="scr_run"):
        st.info(
            "유니버스를 입력하고 실행하세요. AQR 합성점수(가치+모멘텀+퀄리티)와 모멘텀 수익률로 랭크합니다."
        )
        return

    syms = [s.strip().upper() for s in uni.split(",") if s.strip()]
    with st.spinner("바 데이터 로드 중…"):
        bars = _load_universe(catalog, syms, market, live=live)
    if not bars:
        st.error("데이터를 못 불러왔습니다. 심볼/시장 확인 또는 '라이브 페치' 체크 후 재시도.")
        return
    st.caption(f"로드됨: {len(bars)}/{len(syms)} 종목")

    rows = []
    # 모멘텀 랭크 (bars만 필요 — 항상 동작)
    try:
        from engine.portfolio import screen_momentum

        mom = {r.symbol: r for r in screen_momentum(bars, lookback=int(lookback))}
    except Exception as e:
        mom = {}
        st.caption(f"모멘텀 계산 스킵: {e}")
    # AQR 합성 (fundamentals 있으면 가치/퀄리티 포함)
    funds = _load_fundamentals(catalog, list(bars), market)
    aqr = {}
    try:
        from strategies.factor_aqr import rank_aqr_factors

        for r in rank_aqr_factors(bars, funds, lookback=int(lookback)):
            aqr[r.symbol] = r
    except Exception as e:
        st.caption(f"AQR 합성 스킵(펀더멘털 부족 가능): {e}")

    for s in bars:
        m = mom.get(s)
        a = aqr.get(s)
        rows.append(
            {
                "종목": s,
                "현재가": round(m.close, 2) if m else None,
                "모멘텀%": round(m.lookback_return * 100, 2) if m else None,
                "AQR합성": round(a.composite, 3) if a else None,
                "가치": round(a.value, 3) if a else None,
                "퀄리티": round(a.quality, 3) if a else None,
            }
        )
    df = pd.DataFrame(rows)
    sort_col = "AQR합성" if df["AQR합성"].notna().any() else "모멘텀%"
    df = df.sort_values(sort_col, ascending=False, na_position="last").reset_index(drop=True)
    df.insert(0, "순위", df.index + 1)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(
        f"정렬 기준: {sort_col} (펀더멘털 없으면 모멘텀만 — KOSPI/US 펀더멘털은 `trader fundamentals`로 수집)"
    )

    # 차트리딩 연동
    pick = st.selectbox("차트로 볼 종목", [""] + list(df["종목"]), key="scr_pick")
    if pick and st.button(f"▸ {pick} 차트리딩으로", key="scr_to_chart"):
        st.session_state["cr_symbol_in"] = pick
        st.session_state["cr_market_in"] = market
        st.success(f"{pick} 설정 완료 — 상단 '📊 차트리딩' 탭을 눌러 실행하세요.")


# ─────────────────────────────────────────────────────────────────────────────
# 탭 2 — 차트리딩 (기존 로직 유지)
# ─────────────────────────────────────────────────────────────────────────────
def _render_chart_read_tab(catalog) -> None:
    st.subheader("차트 리딩 — SMC/ICT 컨플루언스")
    st.caption(
        "⚠ 차트리딩은 백테스트상 38–42% 적중(엣지 미검증)으로 **참고용**. 진입 판단은 종목선정/추천기 우선."
    )
    st.session_state.setdefault("cr_symbol_in", "BTC/USDT")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        symbol = st.text_input("심볼", key="cr_symbol_in")
    with c2:
        market = st.selectbox("시장", ["crypto", "us", "kospi", "kosdaq"], key="cr_market_in")
    with c3:
        timeframe = st.selectbox("타임프레임", ["1d", "4h", "1h", "15m"], key="cr_tf")
    with c4:
        direction = st.selectbox("방향", ["long", "short"], key="cr_dir")
    if not st.button("차트리딩 실행", type="primary", key="cr_run"):
        st.info("파라미터 설정 후 실행하세요.")
        return

    sym_upper = symbol.strip().upper()
    bars = catalog.get_bars(sym_upper, market=market)
    if not bars:
        try:
            bars = _live_fetch(sym_upper, market, timeframe)
        except Exception as exc:
            st.warning(f"라이브 페치 실패: {exc}")
    if not bars:
        st.error("바 데이터 없음. 심볼/시장 확인 또는 데이터 수집 필요.")
        return
    bars = bars[-300:]

    import plotly.graph_objects as go

    from engine.chart.fvg import run_fvg
    from engine.chart.order_block import detect_order_blocks
    from engine.chart.read import format_chart_read, read_chart
    from engine.chart.volume_profile import build_volume_profile

    with st.spinner("차트리딩 실행 중…"):
        try:
            chart_read = read_chart(bars, direction=direction)
        except Exception as exc:
            st.error(f"차트리딩 실패: {exc}")
            return

    decision_val = chart_read.decision.value
    confluence = chart_read.confluence
    m1, m2, m3 = st.columns(3)
    m1.markdown(f"### {_badge(decision_val)}", unsafe_allow_html=True)
    m2.metric("컨플루언스", f"{confluence:.1f} / 100")
    m3.metric("추세 bias", getattr(chart_read.trend_bias, "value", str(chart_read.trend_bias)))

    ts = [b.ts for b in bars]
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=ts,
                open=[b.open for b in bars],
                high=[b.high for b in bars],
                low=[b.low for b in bars],
                close=[b.close for b in bars],
                name=sym_upper,
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
            )
        ]
    )
    fig.update_layout(
        title=f"{sym_upper} — {timeframe}",
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        height=560,
        margin={"t": 40, "b": 20},
    )
    try:
        for z in run_fvg(bars).active_fvgs:
            if z.mitigated:
                continue
            col = "rgba(38,166,154,0.18)" if z.direction == "bullish" else "rgba(239,83,80,0.18)"
            fig.add_shape(
                type="rect",
                x0=z.ts,
                x1=ts[-1],
                y0=z.zone_low,
                y1=z.zone_high,
                fillcolor=col,
                line={"width": 0},
                layer="below",
            )
    except Exception:
        pass
    try:
        for ob in detect_order_blocks(bars):
            if ob.mitigated:
                continue
            col = "rgba(30,136,229,0.16)" if ob.direction == "bullish" else "rgba(171,71,188,0.16)"
            fig.add_shape(
                type="rect",
                x0=ob.ts,
                x1=ts[-1],
                y0=ob.zone_low,
                y1=ob.zone_high,
                fillcolor=col,
                line={"width": 1, "dash": "dot", "color": "rgba(120,144,200,.5)"},
                layer="below",
            )
    except Exception:
        pass
    try:
        vp = build_volume_profile(bars)
        if not vp.degenerate:
            for y, lbl, w in ((vp.poc_price, "POC", 2), (vp.vah, "VAH", 1), (vp.val, "VAL", 1)):
                fig.add_hline(
                    y=y,
                    line={
                        "color": "rgba(255,235,59,0.6)",
                        "width": w,
                        "dash": "dash" if lbl != "POC" else "solid",
                    },
                    annotation_text=lbl,
                    annotation_position="right",
                )
    except Exception:
        pass
    st.plotly_chart(fig, use_container_width=True)

    if chart_read.contributions:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "신호": c.name,
                        "레이어": c.layer,
                        "가중치": round(c.weight, 3),
                        "방향": c.direction,
                        "메모": c.note,
                    }
                    for c in chart_read.contributions
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    with st.expander("전체 리포트"):
        st.text(format_chart_read(chart_read))


# ─────────────────────────────────────────────────────────────────────────────
# 탭 3 — 추천기 (evaluate_ticker)
# ─────────────────────────────────────────────────────────────────────────────
def _render_recommender(catalog) -> None:
    st.subheader("추천기 — AQR 검증신호 + 정직한 신뢰도 + 진입 플랜")
    c1, c2, c3 = st.columns([2, 1, 3])
    with c1:
        ticker = st.text_input("종목", "NVDA", key="rec_tkr")
    with c2:
        market = st.selectbox("시장", ["us", "kospi", "kosdaq", "crypto"], key="rec_mkt")
    with c3:
        uni = st.text_input(
            "유니버스 컨텍스트 (횡단면 점수용)",
            "MSFT,AAPL,NVDA,AMZN,META,GOOGL,AVGO,TSLA",
            key="rec_uni",
        )
    if not st.button("평가 실행", type="primary", key="rec_run"):
        st.info(
            "종목 평가는 유니버스 대비 횡단면 랭크 + DCF 적정가 + ATR 진입 사다리를 산출합니다."
        )
        return
    try:
        from valuation.recommendation import evaluate_ticker, load_validated_strategy

        strategy = load_validated_strategy()
    except Exception as e:
        st.error(f"검증 전략 로드 실패: {e}")
        return
    syms = [s.strip().upper() for s in uni.split(",") if s.strip()]
    if ticker.strip().upper() not in syms:
        syms.append(ticker.strip().upper())
    with st.spinner("유니버스 로드 + 평가 중…"):
        bars = _load_universe(catalog, syms, market, live=True)
        funds = _load_fundamentals(catalog, list(bars), market)
        try:
            ev = evaluate_ticker(
                ticker.strip().upper(),
                bars_by_symbol=bars,
                fundamentals_by_symbol=funds,
                strategy=strategy,
                asof_ts=datetime.now(tz=UTC).replace(tzinfo=None),
            )
        except Exception as e:
            st.error(f"평가 실패: {e}")
            return
    st.markdown(f"## {ev.ticker} {_badge(ev.action)}", unsafe_allow_html=True)
    m = st.columns(4)
    m[0].metric("신뢰도", f"{ev.confidence}%")
    m[1].metric("랭크", f"{ev.rank}/{ev.universe_size}" if ev.rank else "—")
    m[2].metric("적정가", f"{ev.fair_value:,.2f}" if ev.fair_value else "—")
    cur = ev.current_price or 0
    disc = ((ev.fair_value - cur) / ev.fair_value * 100) if (ev.fair_value and cur) else None
    m[3].metric("현재가", f"{cur:,.2f}", f"{disc:+.1f}% 할인" if disc is not None else None)
    f = st.columns(4)
    f[0].metric("합성", f"{ev.composite:.3f}" if ev.composite is not None else "—")
    f[1].metric("모멘텀", f"{ev.momentum * 100:+.1f}%" if ev.momentum is not None else "—")
    f[2].metric("가치", f"{ev.value:.3f}" if ev.value is not None else "—")
    f[3].metric("퀄리티", f"{ev.quality:.3f}" if ev.quality is not None else "—")
    if ev.entry_plan:
        ep = ev.entry_plan
        st.markdown("**진입 플랜 (ATR 사다리)**")
        st.json(
            {
                k: getattr(ep, k)
                for k in (
                    "target_entry",
                    "stop_loss",
                    "target_exit",
                    "risk_reward",
                    "expected_holding_days",
                )
                if hasattr(ep, k)
            }
        )
    if ev.reasons:
        st.markdown("**근거**")
        for r in ev.reasons:
            st.markdown(f"- {r}")
    if not ev.in_validated_universe:
        st.warning("검증 유니버스 밖 종목 — 신뢰도 25% 상한. 횡단면 신호 신뢰 제한.")


# ─────────────────────────────────────────────────────────────────────────────
# 탭 4 — 검증결과
# ─────────────────────────────────────────────────────────────────────────────
def _render_validation() -> None:
    st.subheader("검증 결과 — chartbloom 실증 · 백테스트")
    sub1, sub2 = st.tabs(["chartbloom A-1/B 검증", "백테스트 산출물 (out/*.csv)"])
    with sub1:
        md = MERR / "CHARTBLOOM_VALIDATION_RESULTS.md"
        if md.exists():
            st.markdown(md.read_text(encoding="utf-8"))
        else:
            st.info(f"검증 문서 없음: {md}")
    with sub2:
        csvs = sorted(OUT_DIR.glob("*.csv")) if OUT_DIR.exists() else []
        if not csvs:
            st.info("out/ 에 백테스트 CSV 없음. `trader walk-forward --csv-output …` 등으로 생성.")
            return
        pick = st.selectbox("백테스트 파일", [c.name for c in csvs], key="val_csv")
        try:
            df = pd.read_csv(OUT_DIR / pick)
            st.caption(f"{pick} — {len(df)} 행 × {len(df.columns)} 열")
            num = df.select_dtypes("number")
            eq = [
                c
                for c in num.columns
                if any(k in c.lower() for k in ("equity", "cum", "nav", "return"))
            ]
            if eq:
                st.line_chart(num[eq])
            st.dataframe(df.tail(200), use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"로드 실패: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 탭 5 — 예측 (금리/CPI)
# ─────────────────────────────────────────────────────────────────────────────
def _render_forecast() -> None:
    st.subheader("예측 — 기준금리 결정 / 물가")
    region = st.radio("지역", ["us", "kr"], horizontal=True, key="fc_region")
    if st.button("금리 결정 예측 실행", type="primary", key="fc_rate"):
        try:
            import sys

            cp = ROOT / "trading-copilot"
            if str(cp) not in sys.path:
                sys.path.insert(0, str(cp))
            from trading_copilot.rate_forecast import rate_forecast

            r = rate_forecast(region=region)
            import plotly.graph_objects as go

            probs = {
                "인하": r.get("cut_prob", 0),
                "동결": r.get("hold_prob", 0),
                "인상": r.get("hike_prob", 0),
            }
            c1, c2 = st.columns([1, 2])
            c1.metric("다음 회의", str(r.get("date", "—")))
            c1.metric("최빈 결정", str(r.get("modal_decision", "—")))
            fig = go.Figure(
                [
                    go.Bar(
                        x=list(probs),
                        y=[v * 100 for v in probs.values()],
                        marker_color=["#26a69a", "#ffca28", "#ef5350"],
                    )
                ]
            )
            fig.update_layout(
                template="plotly_dark",
                height=320,
                yaxis_title="확률 %",
                title=f"{region.upper()} 기준금리 결정 확률",
            )
            c2.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.error(f"금리 예측 실패: {e}")
    st.divider()
    # 원장 트랙레코드
    led = ROOT / "trading-copilot" / "out" / "rate_ledger.jsonl"
    if not led.exists():
        led = OUT_DIR / "rate_ledger.jsonl"
    if led.exists():
        try:
            rows = [json.loads(x) for x in led.read_text().splitlines() if x.strip()]
            st.caption(f"금리 예측 원장: {len(rows)} 기록")
            st.dataframe(pd.DataFrame(rows).tail(50), use_container_width=True, hide_index=True)
        except Exception as e:
            st.caption(f"원장 로드 스킵: {e}")
    else:
        st.info("금리 원장 없음 — cron(`rate-record`)이 적재 중.")


# ─────────────────────────────────────────────────────────────────────────────
# 탭 6 — 페이퍼 원장 (forward-OOS)
# ─────────────────────────────────────────────────────────────────────────────
def _render_ledgers() -> None:
    st.subheader("페이퍼 원장 — forward-OOS 트랙레코드")
    leds = []
    if OUT_DIR.exists():
        leds = sorted(OUT_DIR.glob("*ledger*.jsonl")) + sorted(OUT_DIR.glob("*oos*.jsonl"))
    leds = sorted(set(leds))
    if not leds:
        st.info(
            "out/ 에 원장 없음. cron(chart/chartbloom/paper)이 적재 중. 아직 fresh 신호 대기일 수 있음."
        )
        return
    pick = st.selectbox("원장 파일", [p.name for p in leds], key="led_pick")
    path = OUT_DIR / pick
    try:
        rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    except Exception as e:
        st.error(f"로드 실패: {e}")
        return
    st.metric("기록 수", len(rows))
    if "chartbloom" in pick and rows:
        wf = sum(1 for r in rows if r.get("has_fvg"))
        c = st.columns(2)
        c[0].metric("CHoCH+FVG", wf)
        c[1].metric("CHoCH-noFVG", len(rows) - wf)
        st.caption(
            "성숙분 채점: `python -m scripts.chartbloom_paper_score --tf 4h` (수 주 누적 후 spread 판정)"
        )
    if rows:
        st.dataframe(pd.DataFrame(rows).tail(100), use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# 탭 7 — RAG 챗봇
# ─────────────────────────────────────────────────────────────────────────────
def _render_rag() -> None:
    st.subheader("RAG — 경제분석 챗봇 (메르/호돌이/홍춘욱/chartbloom/퀀트영상)")
    import socket

    up = False
    try:
        with socket.create_connection(("127.0.0.1", 8800), timeout=1):
            up = True
    except OSError:
        up = False
    if up:
        import streamlit.components.v1 as components

        st.success(f"RAG 서버 가동 중 → {RAG_URL}")
        components.iframe(RAG_URL, height=720, scrolling=True)
    else:
        st.warning("RAG 서버 미가동. 아래 명령으로 띄운 뒤 새로고침하세요:")
        st.code('cd "/Users/jjuni/재무관리 모델/merr_corpus/rag" && ./run.sh', language="bash")
        st.markdown(f"또는 별도 브라우저 탭에서 [{RAG_URL}]({RAG_URL}) 열기.")


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(page_title="재무관리 모델", layout="wide", page_icon="📈")
    st.markdown(_CSS, unsafe_allow_html=True)
    st.title("📈 재무관리 모델 — 통합 대시보드")
    catalog = MarketDataCatalog(CATALOG_PATH)

    cov = catalog.coverage()
    k = st.columns(4)
    k[0].metric("카탈로그 종목", len(cov))
    k[1].metric("총 바", f"{sum(c.rows for c in cov):,}")
    mkts = sorted({c.market for c in cov})
    k[2].metric("시장", ", ".join(mkts) if mkts else "—")
    k[3].metric("RAG", "localhost:8800")
    st.markdown(
        '<div class="hdr-sub">종목선정 → 차트리딩 연동 · 추천기 · 검증결과 · 예측 · 페이퍼원장 · RAG · 로컬 전용</div>',
        unsafe_allow_html=True,
    )
    st.divider()

    tabs = st.tabs(
        [
            "🎯 종목선정",
            "📊 차트리딩",
            "💡 추천기",
            "🔬 검증결과",
            "🔮 예측",
            "📒 페이퍼원장",
            "💬 RAG",
            "🗄 카탈로그",
        ]
    )
    with tabs[0]:
        _render_screener(catalog)
    with tabs[1]:
        _render_chart_read_tab(catalog)
    with tabs[2]:
        _render_recommender(catalog)
    with tabs[3]:
        _render_validation()
    with tabs[4]:
        _render_forecast()
    with tabs[5]:
        _render_ledgers()
    with tabs[6]:
        _render_rag()
    with tabs[7]:
        st.subheader("카탈로그 커버리지")
        if cov:
            st.dataframe(
                pd.DataFrame([c.__dict__ for c in cov]), use_container_width=True, hide_index=True
            )
        else:
            st.info("저장된 데이터 없음. `trader ingest --symbols … --market …` 로 수집.")
        st.caption("수집 예: `trader ingest --symbols AAPL,MSFT --market us`")


if __name__ == "__main__":
    main()
