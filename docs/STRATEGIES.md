# 펀드 전략 분석 → 시스템 매핑

## 1. 순수 퀀트 (수학·통계 기반)

### Renaissance Technologies — Medallion Fund
- 30년 평균 수수료 전 66%, 후 39%. 역사상 최고
- 단기 통계적 차익거래 + 머신러닝, 펀더멘털 0%
- 수백만 건 초단기 거래로 미세 가격 비효율 포착

### Two Sigma ($64B AUM)
- AI/ML 중심. 위성사진/신용카드/소셜 등 대안 데이터

### D.E. Shaw ($58.5B AUM)
- 통계적 차익거래 선구자. 시장중립 + 글로벌 매크로 퀀트 혼합

### AQR Capital
- 학계 기반 팩터 투자 (가치/모멘텀/퀄리티/저변동성). 리스크 패리티

## 2. 매크로 / 올웨더

### Bridgewater Pure Alpha (2025 H1 +17%)
- 글로벌 매크로: 금리/통화/원자재/주식
- **Risk Parity (All Weather)**: 자산별 리스크 기여도 균등화. 4사분면(성장↑/↓ × 인플레↑/↓) 대비

## 3. 멀티스트래티지 / Pod 시스템

### Citadel ($397B, 창업 이래 연 19.5%)
- 5코어: 주식/채권매크로/상품/크레딧CB/글로벌퀀트
- 100+ 독립 Pod. 각 팀은 시장중립 알파만, 리스크는 본사 통제

### Millennium / Point72 — 동일 Pod 모델

## 4. 액티비스트 / 집중 투자

### Pershing Square (Bill Ackman) — 연 15.9%
- 5~10개 종목 집중. 대규모 지분 → 이사회 진입 → 자본배분/스핀오프 압박

### Elliott / TCI / Third Point / Starboard — 동일 패턴

## 5. 시장 메이커 / 자기자본거래

### Jane Street — 2024 매출 $205억, 미국 주식 10%
- 매출 70%가 자기자본 위험거래
- ETF 차익거래 + C++ 초저지연 인프라

### Citadel Securities — 미국 주식 거래량 25%
- 고객 주문흐름 기반 시장조성

## 6. 성장주 / 벤처 하이브리드

### Tiger Global, Coatue
- 상장+비상장. 고성장 테크 집중. 변동성 매우 큼

---

## 우리 시스템에 매핑

| 펀드 모델 | 구현 모듈 | 난이도 | M4에서 현실성 |
|-----------|-----------|--------|---------------|
| Renaissance (Stat-Arb) | `strategies/statarb_pairs.py` | 고 | 중 — 마이크로초 불가, 분 단위 stat-arb는 가능 (크립토만) |
| AQR (팩터) | `strategies/factor_aqr.py` (Value+Mom+**Quality**) | **저** | 상 — **가장 먼저 구현 권장**. Quality는 SEC EDGAR/DART 펀더멘털 필요 |
| Bridgewater (Risk Parity) | `strategies/risk_parity.py` | 저 | 상 — 자산배분 로직만 있으면 즉시 |
| Citadel (Pod) | `pod/allocator.py` + `pod/monitor.py` | 중 | 상 — 여러 전략을 묶을 때 자연스럽게 |
| Pershing Square (액티비스트) | `signals/activist_13f.py` + `signals/insider.py` | 중 | 시그널 가능 — SEC Form 4 + 13F + DART 임원보고 |
| Jane Street (마켓메이킹) | `strategies/mm_crypto.py` | 고 | **크립토 한정** — Binance 스프레드 봇 |

## 미시경제 데이터 기반 추가 전략

펀더멘털·공시·인사이더 데이터로 가능한 알파 전략 (자세히는 `MICROECONOMIC_DATA.md`):

| 전략 | 메커니즘 | 모듈 | 데이터 |
|------|---------|------|--------|
| **PEAD** | 어닝 서프라이즈 후 60일 드리프트 | `strategies/pead.py` | FMP earnings + EDGAR/DART |
| **Earnings Revision** | 애널리스트 컨센서스 상향 종목 매수 | `strategies/revisions.py` | FMP / yfinance |
| **Insider Buying** | 내부자 클러스터 매수 (CEO/CFO 동시) | `signals/insider.py` | SEC Form 4 + DART |
| **13F Mirror** | Tiger/Pershing 등 신규 진입 종목 추종 | `signals/activist_13f.py` | SEC EDGAR 13F |
| **Short Squeeze 회피** | 공매도 잔고 급증 종목 숏 회피 | `risk/short_interest.py` | KRX + FINRA |
| **Trends Nowcasting** | 검색량으로 소비재 매출 선행 예측 | `signals/trends_nowcast.py` | Google Trends |

## 거시경제 데이터 기반 추가 전략

거시 시계열로 가능한 자산배분/regime 전략 (자세히는 `MACROECONOMIC_DATA.md`):

| 전략 | 메커니즘 | 모듈 | 데이터 |
|------|---------|------|--------|
| **All Weather (Regime-aware)** | 성장×인플레 4사분면 자산배분 | `strategies/risk_parity.py` + `strategies/regime_switch.py` | FRED GDP/CPI |
| **Macro Momentum** | Yield curve + real rate 모멘텀 | `strategies/macro_momentum.py` | FRED DGS10/DGS2/PCE |
| **Recession Signal** | 10Y-2Y 역전 시 위험자산 축소 | `signals/recession.py` | FRED T10Y2Y |
| **Macro Event** | CPI/FOMC 발표 surprise 단기 채권/달러 | `strategies/macro_event.py` | 발표 캘린더 |
| **Risk Appetite** | Term + Credit spread 위험선호 단계 | `signals/risk_appetite.py` | FRED 스프레드 |
| **FX Exposure** | DXY/원달러 → 한국 수출주 헤지 | `risk/fx_exposure.py` | FRED DXY + ECOS 환율 |

## Sentiment & Flow 기반 추가 전략

자금 흐름과 감정 데이터로 가능한 전략 (자세히는 `SENTIMENT_FLOW.md`):

| 전략 | 메커니즘 | 모듈 | 데이터 |
|------|---------|------|--------|
| **Foreign Flow Momentum** | 한국 외국인 5일 누적 순매수 → 종목 매수 | `signals/foreign_flow.py` | KRX 매매동향 |
| **Retail Contrarian (KR)** | 개인 매수 + 외국인 매도 → 매도 신호 | `signals/retail_contrarian.py` | KRX 매매동향 |
| **COT Commercial Front-running** | 실수요자 극단 포지션 → 선물 가격 선행 | `signals/cot_commercial.py` | CFTC COT |
| **COT Mean Reversion** | Non-Commercial 95th percentile → 역방향 | `signals/cot_extreme.py` | CFTC COT |
| **GDELT Macro Tone** | 글로벌 뉴스 톤 모멘텀 → 위험자산 비중 | `signals/gdelt_tone.py` | GDELT |
| **Event Detection** | 기업 mention 급증 → 이벤트 탐지 | `signals/gdelt_events.py` | GDELT |
| **WSB Squeeze Pre-emption** | Reddit mention surge → 초기 진입 | `signals/wsb_squeeze.py` | Reddit PRAW |

## 패턴 정리

| 전략 | 시간단위 | 우위의 원천 |
|------|----------|------------|
| Medallion형 퀀트 | 초~일 | 초저지연 + 비밀 알고리즘 |
| 팩터/시스템 | 주~년 | 학술적 입증된 프리미엄 |
| 매크로 | 월~년 | 거시 변수 인사이트 + 레버리지 |
| Pod 멀티전략 | 다양 | 분산된 알파 + 엄격한 리스크 통제 |
| 액티비스트 | 1~5년 | 경영 개입으로 가치 실현 |
| 마켓메이커 | 마이크로초 | 주문흐름 + 인프라 + ETF 차익 |

**공통점**: 정보 우위, 인프라 투자, 작은 우위의 레버리지 증폭.
