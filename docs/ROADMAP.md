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

## Stage 2 — AQR 팩터 백테스트

**목표**: 학술적으로 검증된 팩터 전략 1개를 3시장에서 백테스트, Sharpe ≥ 1.0.

**작업**:
- [ ] `strategies/_base.py` — Strategy 추상 클래스 (Nautilus 호환)
- [ ] `strategies/factor_aqr.py` — Value/Momentum/Quality 팩터 결합
  - Value: Earnings Yield (1/PER) — 미국/한국
  - Momentum: 12-1 month return — 3시장 모두
  - Quality: ROE — 미국/한국
- [ ] `engine/backtest.py` — Nautilus 백테스트 러너 + 리포트
- [ ] 리포트: Sharpe, Sortino, MaxDD, Calmar, Information Ratio, Turnover

**검증 기준**:
- [ ] 미국 시장 12-1 모멘텀 Sharpe 0.6~1.0 (학술 결과 일치)
- [ ] 한국 시장 동일 전략 Sharpe ±0.3 이내
- [ ] 크립토 모멘텀 Sharpe ≥ 1.0 (변동성 프리미엄)
- [ ] 백테스트 1년치 < 5초 (Nautilus Rust 엔진 효과 확인)

**Why 이 전략 먼저**: 데이터 한계 무관(일봉만), 학술적 재현성 풍부, 결과가 명확히 검증 가능.

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

## Stage 4 — Pod 멀티전략 + 크립토 추가

**목표**: Citadel Pod 모델로 3+ 전략 동시 운용, 전체 Sharpe > 개별 평균.

**작업**:
- [ ] 추가 전략:
  - `strategies/risk_parity.py` (Bridgewater식 자산배분)
  - `strategies/statarb_pairs.py` (BTC/ETH 페어 — 크립토만)
- [ ] `pod/allocator.py` — Vol-target 리스크 예산 (각 전략 동일 vol 기여)
- [ ] `pod/monitor.py` — 전략별 Sharpe/DD 실시간 추적, 손실 시 자본 회수
- [ ] CCXT Binance 페이퍼 또는 testnet 연동
- [ ] 대시보드 v2 — 전략별 기여도 분해

**검증 기준**:
- [ ] 3+ 전략 동시 운용 1주일 무사고
- [ ] 통합 포트폴리오 Sharpe > 개별 평균 × 1.1 (분산 효과 입증)
- [ ] 한 전략이 큰 DD 시 자본 자동 회수 동작 확인
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
| 1 | 1주 | 3주 |
| 2 | 2주 | 6주 |
| 3 | 1주 | 3주 |
| 4 | 3주 | 9주 |
| 5 | 4주 (관찰 중심) | 12주 |
| **합계** | **11주** | **33주 (~8개월)** |
