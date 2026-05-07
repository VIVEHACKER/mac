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
| AQR (팩터) | `strategies/factor_aqr.py` | **저** | 상 — **가장 먼저 구현 권장** |
| Bridgewater (Risk Parity) | `strategies/risk_parity.py` | 저 | 상 — 자산배분 로직만 있으면 즉시 |
| Citadel (Pod) | `pod/allocator.py` + `pod/monitor.py` | 중 | 상 — 여러 전략을 묶을 때 자연스럽게 |
| Pershing Square (액티비스트) | `signals/activist_13f.py` | — | 자동매매 X. **13F 추적기로 시그널만** |
| Jane Street (마켓메이킹) | `strategies/mm_crypto.py` | 고 | **크립토 한정** — Binance 스프레드 봇 |

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
