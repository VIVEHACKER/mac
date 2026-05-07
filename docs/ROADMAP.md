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
- [ ] `risk/short_interest.py` — 공매도 잔고 급증 모니터 (KRX + FINRA)
- [ ] `pod/allocator.py` — Vol-target 리스크 예산
- [ ] `pod/monitor.py` — 전략별 Sharpe/DD 실시간 추적
- [ ] 대시보드 v2 — 전략별 기여도 + 시그널 알림

**검증 기준**:
- [ ] 5+ 전략 동시 운용 1주일 무사고
- [ ] 통합 포트폴리오 Sharpe > 개별 평균 × 1.1 (분산 효과 입증)
- [ ] 13F 미러링: 분기별 신규 진입 종목 6개월 보유 시 시장 대비 알파 ≥ 2%/년
- [ ] 인사이더 클러스터 매수 후 90일 시장 대비 알파 ≥ 5%
- [ ] 시장간 상관계수가 낮음 검증 (미국/한국/크립토 < 0.5)

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
| 2 (AQR + PEAD 백테스트) | 2주 | 6주 |
| 3 (페이퍼) | 1주 | 3주 |
| 4 (Pod + 시그널) | 3주 | 9주 |
| 5 (라이브, 관찰 중심) | 4주 | 12주 |
| **합계** | **12주** | **36주 (~9개월)** |
