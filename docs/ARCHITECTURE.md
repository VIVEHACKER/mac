# Architecture

## 스택 결정

| 레이어 | 선택 | 이유 |
|--------|------|------|
| 백테스트/페이퍼/라이브 엔진 | **Nautilus Trader** | Rust 코어로 vectorbt급 속도, 페이퍼/라이브가 동일 코드, arm64 네이티브 |
| 데이터 처리 | **Polars** | Rust 백엔드, pandas보다 5~30배 빠름, M4에서 단일 노드로 수억 row 처리 |
| 시계열 저장 | **DuckDB + Parquet** | OLAP, 64GB RAM에 거의 다 캐시 가능, SQL 인터페이스 |
| ML | **PyTorch (MPS)** + XGBoost | M4 GPU 활용. CUDA 환경과 코드 호환 |
| UI | **Streamlit** | 가장 빠른 개발. 대시보드 한 두 시간이면 |
| 모니터링 | **Grafana + Prometheus** (선택) | 라이브 운영 시 |

## 시스템 다이어그램

```
┌─────────────────────────────────────────────────┐
│           Streamlit Dashboard (UI)              │
│   PnL / 포지션 / 알파별 기여 / 리스크 메트릭     │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│         Strategy Layer (Python)                 │
│  ┌──────────┬──────────┬──────────┬──────────┐  │
│  │ Factor   │ Stat-Arb │ Momentum │ Risk     │  │
│  │ (AQR)    │ (RenTec) │ XS       │ Parity   │  │
│  └──────────┴──────────┴──────────┴──────────┘  │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│      Nautilus Trader Engine (Rust core)         │
│   ┌──────────┬──────────┬──────────┐            │
│   │ Backtest │  Paper   │   Live   │            │
│   │  Runner  │  Runner  │  Runner  │ ← 동일 IF │
│   └──────────┴──────────┴──────────┘            │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│  Data Layer: DuckDB + Parquet (~60% RAM cache)  │
│  ┌─────────┬──────────┬──────────┐              │
│  │ Alpaca  │ pykrx    │  CCXT    │              │
│  │ (US)    │ (KR EOD) │ (Crypto) │              │
│  └─────────┴──────────┴──────────┘              │
└─────────────────────────────────────────────────┘
```

## 디렉토리 트리

```
trader/
├── pyproject.toml           # uv 또는 poetry
├── .env.example             # API 키 템플릿
├── .gitignore
├── CLAUDE.md                # 다음 세션 컨텍스트
├── data/
│   ├── ingest/              # 시장 + 미시경제 수집기
│   │   ├── alpaca_us.py     # 미국 주식 가격
│   │   ├── pykrx_kr.py      # 한국 주식 가격 + 시총/PER/PBR
│   │   ├── ccxt_crypto.py   # 크립토 가격
│   │   ├── edgar_us.py      # SEC EDGAR (10-K/Q, 8-K, Form 4, 13F)
│   │   ├── dart_kr.py       # DART OpenAPI (한국 공시 + 임원보고)
│   │   ├── fmp_earnings.py  # FMP 어닝/가이던스/컨센서스
│   │   ├── trends_alt.py    # Google Trends (대안 데이터)
│   │   ├── onchain_eth.py   # Etherscan (크립토 온체인)
│   │   ├── fred_macro.py    # FRED + ALFRED vintage (미국 매크로)
│   │   ├── ecos_kr.py       # 한국은행 ECOS REST (한국 매크로)
│   │   ├── macro_calendar.py # 발표 캘린더 + surprise 계산
│   │   ├── krx_flows.py     # KRX 투자자별 매매 (외인/기관/연기금/개인)
│   │   ├── cot_cftc.py      # CFTC COT 주간 선물 포지션
│   │   ├── gdelt_news.py    # GDELT 글로벌 뉴스 톤 + 기업 mention
│   │   ├── reddit_mentions.py # Reddit WSB/stocks 종목 mention
│   │   ├── crypto_microstructure.py # CCXT funding rate + OI + L/S ratio
│   │   ├── crypto_orderbook.py  # CCXT fetch_order_book → OrderBookSnapshot (크립토 전용)
│   │   ├── crypto_open_interest.py # CCXT fetch_open_interest_history + to_perp_symbol (크립토 전용)
│   │   ├── cboe_options.py  # CBOE VIX, VIX9D/3M/6M, SKEW, Put/Call CSV
│   │   ├── deribit_options.py # Deribit BTC/ETH 옵션 + DVOL
│   │   └── option_chain.py  # Yahoo (US) + KRX (KR) 종목별 옵션 체인
│   ├── store/               # Parquet (시계열) + DuckDB (메타)
│   │   ├── eod/             # 일봉
│   │   ├── intraday/        # 분봉 (크립토 위주)
│   │   ├── fundamentals/    # 분기 재무제표 (point-in-time)
│   │   ├── filings/         # 공시 raw 텍스트
│   │   ├── events/          # 어닝/가이던스/Form 4
│   │   ├── macro/           # 매크로 시계열 + regime
│   │   ├── sentiment/       # KRX flows + COT + GDELT + Reddit
│   │   ├── derivatives/     # Crypto funding/OI/L-S, 옵션 sentiment, 옵션 체인
│   │   └── catalog.duckdb
│   ├── models.py            # 공유 데이터 모델: OrderBookLevel, OrderBookSnapshot, OpenInterestRecord
│   └── catalog.py           # 통합 카탈로그 — `as_of=` 강제로 look-ahead 방지
├── valuation/               # 신규: 적정가/평가/진입가 모듈
│   ├── _base.py             # Valuator 추상 클래스
│   ├── dcf.py               # DCF 계산 + WACC/growth 민감도
│   ├── multiples.py         # P/E, EV/EBITDA, P/B, P/S peer 비교
│   ├── rim.py               # Residual Income Model (금융주 강함)
│   ├── crypto_valuation.py  # NVT, MVRV, S2F, DeFi PE
│   ├── peer_groups.py       # GICS + 시총 ±50% peer 선택
│   ├── composite.py         # 산업별 가중 통합 fair value
│   ├── score.py             # z-score → -3~+3 rating
│   └── entry.py             # MoS + technical + ATR ladder
├── strategies/
│   ├── _base.py             # Strategy 추상 클래스
│   ├── factor_aqr.py        # Value + Momentum + Quality (펀더멘털 활용)
│   ├── statarb_pairs.py     # 통계적 차익거래 (크립토부터)
│   ├── risk_parity.py       # Bridgewater식 자산배분 (regime-aware)
│   ├── regime_switch.py     # 매크로 4사분면 기반 자산배분 전환
│   ├── macro_momentum.py    # 매크로 모멘텀 (yield curve, real rate)
│   ├── macro_event.py       # CPI/FOMC 발표 surprise 단기 트레이딩
│   ├── momentum_xs.py       # 횡단면 모멘텀
│   ├── pead.py              # 어닝 서프라이즈 드리프트
│   ├── revisions.py         # Earnings revision 모멘텀
│   ├── funding_arb.py       # Crypto perp 펀딩-스팟 차익 (펀딩 받으며 spot 매수)
│   ├── iv_crush.py          # 어닝 임박 IV 급등 → 발표 후 매도
│   ├── value_long.py        # valuation rating ≥ +2 종목 매수 (Buffett-style)
│   └── ml_xgboost.py        # XGBoost 시그널 (MPS) — 펀더멘털 + 가격 + 매크로 + valuation 결합
├── signals/                 # 🔲 설계 트리(21) — 구현 3: foreign_flow, vix_term·vix_peak(✅ 정보게이트 INFORMATIVE)
│                            #    (신규 신호는 검증 게이트 통과 전 자금배분 금지 — advisory only)
│   ├── activist_13f.py      # 13F 신규/증가 — Pershing/Tiger 미러
│   ├── insider.py           # Form 4 + DART 임원보고 — 클러스터 매수
│   ├── revisions.py         # 애널리스트 컨센서스 상향
│   ├── trends_nowcast.py    # Google Trends → 매출 선행 예측
│   ├── recession.py         # Yield curve inversion 침체 선행 신호
│   ├── risk_appetite.py     # Term spread + Credit spread → 위험선호 단계
│   ├── foreign_flow.py      # KRX 외국인 순매수 누적 모멘텀 (한국 강력)
│   ├── institution_flow.py  # KRX 기관 순매수 모멘텀
│   ├── retail_contrarian.py # KRX 개인 매수 + 외국인 매도 → 역지표
│   ├── cot_commercial.py    # CFTC Commercial 극단 → 가격 압력 선행
│   ├── cot_extreme.py       # Non-Commercial 95th → mean reversion
│   ├── gdelt_tone.py        # GDELT 매크로/기업 톤 모멘텀
│   ├── gdelt_events.py      # GDELT mention 급증 이벤트 detection
│   ├── wsb_squeeze.py       # Reddit WSB mention surge → squeeze 사전 포착
│   ├── funding_extreme.py   # Crypto perp 펀딩 극단 → 청산 위험
│   ├── oi_squeeze.py        # OI 급증 + 가격 횡보 → 변동성 확장
│   ├── ls_ratio.py          # 거래소 long/short 극단 → mean reversion
│   ├── vix_term.py          # VIX/VIX3M 백워데이션 → 단기 반등
│   ├── vix_peak.py          # VIX 정점 후 하락 → 위험자산 진입
│   ├── put_call_extreme.py  # Put/Call > 1.2 극단 비관 → contrarian
│   └── btc_vol.py           # Deribit DVOL 극단 → BTC 변동성 평균회귀
├── engine/
│   ├── backtest.py          # 🔲 현재 stdlib 벡터 루프 (Nautilus 미연결 — #4에서 통합)
│   ├── paper.py             # ✅ Alpaca paper / Binance testnet
│   ├── live.py              # ⚠️ broker 교체식 — paper와 공통 Strategy 인터페이스 미완 (#4)
│   └── chart/               # 차트 리딩 엔진 (순수 stdlib, 시장 무관)
│       ├── types.py         # 공유 열거형(TrendBias/EntryState/OIQuadrant 등), PriceBar 지오메트리 헬퍼, confluence_score, decide_entry_state, ChartRead/SignalContribution/EntryContext
│       ├── structure.py     # 스윙 구조 / BOS / CHoCH / EQH-EQL
│       ├── fvg.py           # Fair Value Gap + IFVG
│       ├── order_block.py   # 오더블록 + 브레이커
│       ├── liquidity.py     # 유동성 풀·스윕 / 프리미엄-디스카운트 / OTE / MSS
│       ├── volume_profile.py # POC / Value Area / HVN-LVN (매물대)
│       ├── volume.py        # RVOL / OBV / CMF / 클라이맥스 / No-Supply·No-Demand / VDU / 다이버전스
│       ├── wyckoff.py       # 매집·분산 스키매틱 / Phase A–E / Spring·UTAD
│       ├── patterns.py      # 더블탑·바텀 / 헤숄 / 삼각형·쐐기·플래그·렉탱글·컵&핸들
│       ├── candles.py       # 단일·복합·삼선 캔들 패턴
│       ├── orderbook.py     # L2 OBI / VAMP / 호가벽 — 크립토 전용(ccxt)
│       ├── open_interest.py # OI 4사분면 / 스퀴즈·캐스케이드 / 펀딩 — 크립토 전용(ccxt)
│       └── read.py          # 컨플루언스 집계 → EntryState(ENTER_NOW/SCALE_IN/WAIT_FOR_PULLBACK/AVOID) + 진입 가격대·인밸리데이션·근거
├── pod/                     # Citadel 스타일 멀티전략
│   ├── allocator.py         # Vol-target 리스크 예산 분배
│   └── monitor.py           # Pod별 PnL/DD/Sharpe 추적
├── risk/                    # 구현(✅)은 원 설계와 다름 — 라이브-운영 중심으로 진화
│   ├── kill_switch.py       # ✅ 일일/피크 DD + gross exposure 자동 정지
│   ├── pretrade.py          # ✅ 프리트레이드 게이트 (halt/notional/weight/buying-power/short/PDT)
│   ├── halt_state.py        # ✅ 영구 halt latch (JSON)
│   ├── equity_track.py      # ✅ 에쿼티/피크 추적
│   ├── policy.py            # ✅ default-live-v1 리스크 파라미터
│   ├── shortability.py      # ✅ 공매도 가능 여부
│   ├── sizing.py            # ✅ half-Kelly + vol-target + risk-cap + 농도캡
│   ├── exposure.py          # ✅ gross/net/단일종목/섹터 노출 모니터 + 한도 체크
│   ├── slippage.py          # ✅ half-spread+√참여율 임팩트 (옵트인, 기본 OFF)
│   ├── short_interest.py    # 🔲 설계 — 공매도 잔고 급증 모니터 (미구현)
│   ├── option_skew.py       # 🔲 설계 — 옵션 IV skew 하방 신호 (미구현)
│   ├── fx_exposure.py       # 🔲 설계 — DXY/원달러 환율 노출 (미구현)
│   └── tail_risk.py         # 🔲 설계 — CBOE SKEW > 140 꼬리위험 (미구현)
├── dashboard/
│   └── app.py               # Streamlit 단일 진입점
├── infra/
│   ├── docker-compose.yml   # Postgres + Grafana (선택)
│   └── secrets.env.example
└── tests/
    ├── test_strategies/
    ├── test_data/
    └── test_engine/
```

## 데이터 흐름

```
1. 수집 (cron 또는 manual):
   Alpaca/pykrx/CCXT → ingest/ → Parquet (날짜 파티션)

2. 카탈로그:
   data/catalog.py → DuckDB 메타테이블 (symbol, market, asof, source)

3. 백테스트:
   Strategy → engine/backtest.py → Nautilus Backtest Engine
   → Polars로 결과 분석 → 리포트 (Sharpe, DD, Calmar, IR)

4. 페이퍼:
   동일 Strategy → engine/paper.py → Alpaca paper / Binance testnet
   → 실시간 PnL → DuckDB + Streamlit

5. 라이브:
   페이퍼 검증 후 → engine/live.py (broker config만 교체)
   → kill_switch 활성 → 실거래
```

## 핵심 설계 원칙

1. **단일 Strategy 인터페이스** — 백테스트/페이퍼/라이브 코드 동일
2. **데이터 카탈로그 중앙집중** — 시장별 데이터 형태 차이를 catalog.py가 흡수
3. **Point-in-time 보장** — 펀더멘털·공시는 발표일(asof) 이후만 사용. look-ahead bias 차단
4. **Pod 모듈 분리** — 전략은 알파 생성만, 자본배분/리스크는 pod/risk가 담당
5. **Valuation 분리** — fair value 계산은 strategies/와 독립. 입력값(WACC/growth/peer) 모두 기록해 재현성 보장
6. **Kill switch 기본 활성** — 라이브에선 일일 DD/포지션 한도 강제
7. **테스트 우선** — strategies/는 모두 fixture 기반 테스트 (TDD)
8. **차트 엔진 stdlib 전용** — `engine/chart/`는 외부 TA 라이브러리 없이 stdlib(statistics, math)만 사용. 재현성과 백테스트 통합이 목적이므로 pandas/numpy 의존을 배제한다.

## 차트 리딩 엔진 설계 결정

### 시장 범용 vs. 크립토 전용 분리
OHLCV 기반 탐지기(structure / fvg / order_block / liquidity / volume_profile / volume / wyckoff / patterns / candles)는 미국 주식·한국 주식·크립토 모두에서 동일하게 동작한다. 호가(orderbook.py)와 미체결약정/펀딩(open_interest.py)은 ccxt를 통한 크립토 영구선물 거래소에서만 수집 가능하므로 크립토 전용으로 분리한다. `chart-read` CLI의 `--with-orderbook` / `--with-oi` 플래그는 `--market crypto`일 때만 유효하다.

### PriceBar.ts 인트라데이 설계
일봉은 `ts: date`(ISO date)로 충분하지만, 4h/1h/15m 등 인트라데이 타임프레임에서는 날짜만으로 봉을 구분할 수 없다. `ccxt_crypto.py`의 `fetch_ccxt_bars(..., intraday=True)`는 CCXT OHLCV 응답의 밀리초 타임스탬프를 `datetime`으로 변환해 `PriceBar.ts`에 저장함으로써 인트라데이 시퀀스의 순서를 보존한다. 일봉 경로는 기존 `date` 타입을 유지해 하위 호환을 깨지 않는다.

### 컨플루언스 집계
`read.py`는 각 개념 탐지기에서 `SignalContribution(concept, weight, vote, reason)` 리스트를 수집한 뒤 `confluence_score()`로 0–100 정규화 점수를 계산하고 `decide_entry_state()`로 `EntryState`를 결정한다. 개념별 기본 가중치는 `types.py`에 상수로 선언되어 있으며 호출 측에서 override 가능하다.
