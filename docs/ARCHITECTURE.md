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
│   └── catalog.py           # 통합 카탈로그 — `as_of=` 강제로 look-ahead 방지
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
│   └── ml_xgboost.py        # XGBoost 시그널 (MPS) — 펀더멘털 + 가격 + 매크로 결합
├── signals/                 # 미시·거시·sentiment 기반 알파/시그널
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
│   ├── backtest.py          # Nautilus 백테스트 러너
│   ├── paper.py             # Alpaca paper / Binance testnet
│   └── live.py              # 동일 코드, broker만 교체
├── pod/                     # Citadel 스타일 멀티전략
│   ├── allocator.py         # Vol-target 리스크 예산 분배
│   └── monitor.py           # Pod별 PnL/DD/Sharpe 추적
├── risk/
│   ├── kill_switch.py       # 일일 DD > X% 자동 정지
│   ├── exposure.py          # 시장/섹터/팩터 노출 모니터
│   ├── slippage.py          # 슬리피지 모델
│   ├── short_interest.py    # 공매도 잔고 급증 모니터
│   ├── option_skew.py       # 옵션 IV skew 하방 신호
│   ├── fx_exposure.py       # DXY/원달러 환율 노출 (한국 수출주)
│   └── tail_risk.py         # CBOE SKEW > 140 꼬리위험 모니터
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
5. **Kill switch 기본 활성** — 라이브에선 일일 DD/포지션 한도 강제
6. **테스트 우선** — strategies/는 모두 fixture 기반 테스트 (TDD)
