# 5단계 구현 로드맵

각 단계는 **검증 기준 통과 후에만** 다음으로. "아마 될거야" 금지.

## Stage 1 — 데이터 인프라

**목표**: 3시장(미국/한국/크립토) 일봉 10년치 + 크립토 분봉 1년치 수집, DuckDB 인덱싱.

**작업**:
- [ ] `data/ingest/alpaca_us.py` — Alpaca SDK로 S&P500 + 주요 ETF 일봉
- [ ] `data/ingest/pykrx_kr.py` — KOSPI200 + KOSDAQ150 일봉 + 시총
- [ ] `data/ingest/ccxt_crypto.py` — Binance 상위 30개 페어 일봉/1분봉
- [ ] `data/catalog.py` — 시장 일관 인터페이스 (`get_bars(symbol, market, freq, start, end)`)
- [ ] DuckDB 메타테이블 + Parquet 파티션
- [ ] `tests/test_data/` — 각 소스 fixture 테스트

**검증 기준**:
- [ ] 미국 S&P500 10년치 SQL 쿼리 1초 이내 응답
- [ ] 한국 KOSPI200 10년치 동일
- [ ] BTC/ETH 1분봉 1년치 메모리에 < 5초 로드
- [ ] catalog 인터페이스로 3시장 데이터 동일하게 조회 가능

---

## Stage 1.5 — 미시경제 데이터 인프라

**목표**: 펀더멘털 + 공시 + 인사이더 데이터를 point-in-time 보장으로 수집·저장.

**작업**:
- [ ] `data/ingest/edgar_us.py` — SEC EDGAR 10-K/Q 파서 (재무제표 추출)
- [ ] `data/ingest/dart_kr.py` — DART OpenAPI (사업보고서 + 임원·주요주주 보고)
- [ ] `data/ingest/fmp_earnings.py` — FMP 어닝 캘린더 + 컨센서스 + 가이던스
- [ ] `data/ingest/edgar_us.py`에 Form 4 (인사이더) + 13F 추가
- [ ] DuckDB 테이블: `fundamentals_q`, `earnings`, `insider_trades`, `holdings_13f`
- [ ] `data/catalog.py`에 `as_of=` 파라미터 강제 (point-in-time)
- [ ] `tests/test_data/test_pit.py` — look-ahead 회귀 테스트

**검증 기준**:
- [ ] S&P500 + KOSPI200 분기 펀더멘털 10년치 수집
- [ ] `get_fundamentals(symbol, as_of=date)` 가 발표일 이후 데이터만 반환 (회귀 테스트 통과)
- [ ] FMP 무료 한도(250 req/day) 내에서 일일 갱신 자동화
- [ ] DART API 호출 ↔ pykrx 펀더멘털 교차 검증 ±5% 이내

**왜 Stage 2 전에**: AQR Quality 팩터는 ROE/FCF가 필요. 펀더멘털 없이는 모멘텀+가치만 가능.

---

## Stage 1.6 — 거시경제 데이터 인프라

**목표**: FRED + 한국은행 ECOS에서 핵심 매크로 시계열을 vintage 보존하며 수집, regime 분류 가능.

**작업**:
- [ ] `data/ingest/fred_macro.py` — FRED + ALFRED vintage API
  - 필수 시리즈 15개 (DFF, DGS10, DGS2, T10Y2Y, CPIAUCSL, PCEPILFE, UNRATE, PAYEMS, GDPC1, M2SL, DTWEXBGS, VIXCLS 등)
- [ ] `data/ingest/ecos_kr.py` — 한국은행 ECOS REST 호출
  - 필수 시리즈 8개 (정책금리, 국고채 3Y/10Y, CPI, 실업률, 산업생산, 환율, 경상수지)
- [ ] `data/ingest/macro_calendar.py` — 발표 캘린더 + actual/consensus → surprise 계산
- [ ] DuckDB 테이블: `macro_series`, `macro_releases`, `macro_regime`
- [ ] `data/catalog.py`에 `get_macro(series_id, as_of=, use_vintage=True)` 추가
- [ ] regime classifier: GDP YoY × CPI YoY → 4사분면 + confidence
- [ ] `tests/test_data/test_macro_pit.py` — vintage 회귀 테스트

**검증 기준**:
- [ ] FRED 핵심 15개 시리즈 30년치 수집
- [ ] ECOS 핵심 8개 시리즈 20년치 수집 (한국은 미국보다 짧음)
- [ ] `get_macro(series_id="GDPC1", as_of="2008-09-15")` → 발표된 적 있는 값만 반환 (vintage 동작 확인)
- [ ] 2008-2009 침체기 regime이 "성장↓" 사분면으로 분류됨
- [ ] CPI 발표 시각 lag 적용 — `release_ts` 이전 데이터로 거래 시 회귀 테스트 fail

**왜 Stage 1.5 직후**: AQR 팩터(Stage 2)는 매크로 없이도 가능하지만, Risk Parity와 regime-aware 전략은 매크로가 필수. Stage 4 멀티전략 들어가기 전에 인프라 완성 필요.

---

## Stage 1.7 — Sentiment & Flow 데이터

**목표**: 자금 흐름(KRX 기관매매, CFTC COT) + 감정(GDELT, Reddit) 데이터 수집·인덱싱.

**작업**:
- [ ] `data/ingest/krx_flows.py` — pykrx 투자자별 매매 (시장 + 종목 단위, 5년치)
  - 외국인 / 기관 / 연기금 / 개인 / 금융투자 / 일반법인 분리
- [ ] `data/ingest/cot_cftc.py` — CFTC COT 주간 보고 (legacy + disaggregated)
  - 주요 계약: S&P 500, Nasdaq, Gold, Crude Oil, KRW, 10Y Note, DXY
- [ ] `data/ingest/gdelt_news.py` — GDELT 톤 + 기업 mention
  - BigQuery 무료 티어 또는 CSV 직접 다운로드
  - 주요 테마: ECON_INFLATION, ECON_BANKRUPTCY, FED_RATE, CRISIS
- [ ] `data/ingest/reddit_mentions.py` — Reddit PRAW
  - 서브레딧: wallstreetbets, stocks, investing, cryptocurrency
  - 종목 ticker 추출 + bullish/bearish 키워드 매칭 + bot 필터
- [ ] DuckDB 테이블: `krx_flows`, `cot_positions`, `gdelt_tone`, `gdelt_mentions`, `reddit_mentions`
- [ ] `data/catalog.py`에 sentiment/flow 조회 API 추가
- [ ] `tests/test_data/test_flow_pit.py` — KRX 발표 시각, COT 금요일 release_ts 회귀 테스트

**검증 기준**:
- [ ] KRX KOSPI200 + KOSDAQ150 5년치 투자자별 매매 수집
- [ ] COT 주요 계약 7개 5년치 (Disaggregated 우선)
- [ ] GDELT 미국/한국 톤 + S&P500 + KOSPI200 종목 mention 1년치
- [ ] Reddit WSB/stocks 30일치 종목 mention (백필)
- [ ] PIT 회귀 테스트 통과 — KRX 18시 이전 사용 시 fail
- [ ] Bot 필터: Reddit 스팸 점수 > 0.7 계정 자동 제외

**왜 이 시점**: 미시·매크로 데이터로 알파 백테스트(Stage 2-3)는 가능하지만, sentiment/flow는 Stage 4 Pod 멀티전략에서 강력한 보완 알파. Stage 4 들어가기 전에 인프라 완성.

---

## Stage 2 — AQR 팩터 백테스트 (Value + Momentum + Quality)

**목표**: 학술적으로 검증된 3-팩터 전략을 3시장에서 백테스트, Sharpe ≥ 1.0.

**작업**:
- [ ] `strategies/_base.py` — Strategy 추상 클래스 (Nautilus 호환)
- [ ] `strategies/factor_aqr.py` — Value/Momentum/Quality 결합
  - Value: Earnings Yield (1/PER), Book/Price — 미국/한국
  - Momentum: 12-1 month return — 3시장 모두
  - Quality: ROE, FCF/EV, Asset Turnover — Stage 1.5 펀더멘털 활용
- [ ] `strategies/pead.py` — 어닝 서프라이즈 드리프트
  - 발표 후 +2 ~ +60일 보유, surprise top decile 매수
- [ ] `engine/backtest.py` — Nautilus 백테스트 러너 + 리포트
- [ ] 리포트: Sharpe, Sortino, MaxDD, Calmar, Information Ratio, Turnover

**검증 기준**:
- [ ] 미국 12-1 모멘텀 Sharpe 0.6~1.0 (학술 결과 일치)
- [ ] 미국 AQR 3팩터 Sharpe > 단일 팩터 평균 (분산 효과)
- [ ] PEAD 미국 Sharpe ≥ 0.7 (Bernard-Thomas 1989 재현)
- [ ] 한국 동일 전략 Sharpe ±0.3 이내
- [ ] 크립토 모멘텀 Sharpe ≥ 1.0
- [ ] 백테스트 1년치 < 5초

**Why 이 전략 먼저**: 학술적 재현성 풍부, 점진적 확장 (모멘텀만 → +가치 → +퀄리티 → +PEAD).

---

## Stage 3 — Alpaca 페이퍼 트레이딩

**목표**: Stage 2 검증된 전략을 Alpaca 페이퍼 계정에서 실시간 시그널 송출, 7일 무사고.

**작업**:
- [ ] `engine/paper.py` — Alpaca paper broker 어댑터
- [ ] `risk/kill_switch.py` — 일일 DD > 2%, 포지션 > 자본 100% 시 정지
- [ ] `dashboard/app.py` v1 — 실시간 PnL, 현재 포지션, 시그널 알림
- [ ] 시그널 → 주문 실행 latency 측정

**검증 기준**:
- [ ] 7일 연속 무사고 (체결 실패율 < 1%)
- [ ] 슬리피지 < 5bp (Alpaca 일봉 실행 기준)
- [ ] 페이퍼 PnL이 백테스트 예측 ± 10% 이내
- [ ] kill_switch 한 번 이상 강제 발동 테스트 (가짜 큰 손실 주입)

---

## Stage 4 — Pod 멀티전략 + 액티비스트/인사이더 시그널 추가

**목표**: Citadel Pod 모델로 5+ 전략 동시 운용, 미시경제 시그널 통합.

**작업**:
- [ ] 추가 전략:
  - `strategies/risk_parity.py` (Bridgewater식 자산배분)
  - `strategies/statarb_pairs.py` (BTC/ETH 페어 — 크립토만)
- [ ] `signals/activist_13f.py` — SEC EDGAR 13F 분기별 파싱
  - Pershing/Tiger/Elliott 신규 진입 종목 추적
- [ ] `signals/insider.py` — Form 4 + DART 임원보고
  - CEO/CFO 클러스터 매수 알림 (3명 이상 동시 매수)
- [ ] `signals/revisions.py` — FMP 컨센서스 변경 모멘텀
- [ ] `signals/recession.py` — Yield curve inversion 모니터 (Stage 1.6 매크로 활용)
- [ ] `signals/risk_appetite.py` — Term + Credit spread → 위험선호 단계
- [ ] `signals/foreign_flow.py` — KRX 외국인 N일 누적 모멘텀 (한국 종목 알파)
- [ ] `signals/cot_commercial.py` — Commercial 극단 포지션 → 가격 선행
- [ ] `signals/gdelt_tone.py` — 매크로 sentiment 모멘텀 + 이벤트
- [ ] `signals/wsb_squeeze.py` — Reddit mention surge → 사전 포착
- [ ] `strategies/regime_switch.py` — 매크로 4사분면 기반 자산배분 전환
- [ ] `strategies/macro_momentum.py` — Yield curve + real rate 모멘텀
- [ ] `risk/short_interest.py` — 공매도 잔고 급증 모니터 (KRX + FINRA)
- [ ] `risk/fx_exposure.py` — DXY/원달러 노출 모니터 (한국 수출주)
- [ ] `pod/allocator.py` — Vol-target + regime-aware 리스크 예산
- [ ] `pod/monitor.py` — 전략별 Sharpe/DD 실시간 추적
- [ ] 대시보드 v2 — 전략별 기여도 + 시그널 알림 + 현재 매크로 regime

**검증 기준**:
- [ ] 7+ 전략 동시 운용 1주일 무사고
- [ ] 통합 포트폴리오 Sharpe > 개별 평균 × 1.1 (분산 효과 입증)
- [ ] 13F 미러링: 분기별 신규 진입 종목 6개월 보유 시 시장 대비 알파 ≥ 2%/년
- [ ] 인사이더 클러스터 매수 후 90일 시장 대비 알파 ≥ 5%
- [ ] regime classifier가 2020-03 (코로나) 시점에 "성장↓ 인플레↓" 사분면으로 분류
- [ ] regime-aware risk parity가 정적 risk parity 대비 MaxDD ≥ 20% 개선
- [ ] 시장간 상관계수가 낮음 검증 (미국/한국/크립토 < 0.5)
- [ ] 한국 외국인 5일 누적 순매수 시그널 백테스트 시 Sharpe ≥ 0.5
- [ ] COT Commercial 극단 포지션 후 60일 평균회귀 동작 (Gold/Oil 선물)
- [ ] WSB mention z-score ≥ 3 종목 30일 보유 알파 ≥ 5% (단, drawdown 큼 — 소액만)

---

## Stage 5 — 라이브 (소액 시작)

**목표**: 실자금 5%로 시작, 1개월 운영, 페이퍼 대비 편차 < 10%.

**작업**:
- [ ] `engine/live.py` — paper.py와 broker config만 차이
- [ ] 라이브 전 체크리스트:
  - [ ] 모든 API 키는 IP whitelist + 출금 권한 OFF
  - [ ] kill_switch 테스트 통과
  - [ ] 자본의 5%만 입금
  - [ ] 일일 손실 한도 = 입금액의 2%
- [ ] 시작:
  - 크립토부터 (CCXT, 가장 검증 수월)
  - 미국 주식 (Alpaca, 일봉 전략)
  - 한국은 KIS API 추가 후 (선택)

**검증 기준**:
- [ ] 1개월 운영, 페이퍼 PnL 대비 ±10% 이내
- [ ] 슬리피지 모델이 실제 슬리피지를 ±20%로 예측
- [ ] kill_switch 한 번도 부주의로 발동 안 함
- [ ] 알파가 검증되면 자본 단계적 증액 (5% → 20% → 50%)

---

## 비검증 작업 (로드맵 외)

- HFT/마이크로초 전략 (인프라 비현실적)
- 옵션/파생 (브로커 한계)
- 액티비스트 자동화 (수동 영역)

이런 건 시그널/대시보드로만 (예: 13F 파일링 알림, IV 스크리너).

---

## 시간 예상 (참고)

| Stage | 풀타임 | 주말 작업 |
|-------|--------|-----------|
| 1 (가격 데이터) | 1주 | 3주 |
| 1.5 (미시경제 데이터) | 1주 | 3주 |
| 1.6 (거시경제 데이터 + regime) | 1주 | 3주 |
| 1.7 (sentiment & flow) | 1주 | 3주 |
| 2 (AQR + PEAD 백테스트) | 2주 | 6주 |
| 3 (페이퍼) | 1주 | 3주 |
| 4 (Pod + 시그널 + regime + flow) | 4주 | 12주 |
| 5 (라이브, 관찰 중심) | 4주 | 12주 |
| **합계** | **15주** | **45주 (~11개월)** |
