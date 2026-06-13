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

## 파생·마이크로구조 기반 추가 전략

옵션·perp 마이크로구조 데이터로 가능한 전략 (자세히는 `DERIVATIVES_DATA.md`):

| 전략 | 메커니즘 | 모듈 | 데이터 |
|------|---------|------|--------|
| **Funding Arbitrage** | Crypto perp 펀딩 받으며 spot 매수 (델타 헤지) | `strategies/funding_arb.py` | CCXT funding |
| **Funding Extreme** | 펀딩 양수 극단 → 롱 청산 위험 매도 | `signals/funding_extreme.py` | CCXT funding |
| **OI Squeeze** | OI 급증 + 가격 횡보 → 변동성 확장 임박 | `signals/oi_squeeze.py` | CCXT OI |
| **L/S Mean Reversion** | 거래소 long/short 극단 → 역방향 | `signals/ls_ratio.py` | Binance/Bybit |
| **VIX Term Reversal** | VIX/VIX3M < 0.85 백워데이션 → 단기 반등 | `signals/vix_term.py` | CBOE |
| **Put/Call Contrarian** | Put/Call > 1.2 극단 비관 → 매수 | `signals/put_call_extreme.py` | CBOE |
| **IV Crush** | 어닝 임박 IV 급등 → 발표 후 매도 | `strategies/iv_crush.py` | yfinance options |
| **Tail Risk Monitor** | CBOE SKEW > 140 → 헤지 비중 ↑ | `risk/tail_risk.py` | CBOE |
| **BTC Vol Mean Reversion** | DVOL 극단 → 평균회귀 | `signals/btc_vol.py` | Deribit |

## Valuation 기반 추가 전략

DCF/Multiples/RIM 통합 fair value로 가능한 전략 (자세히는 `VALUATION.md`):

| 전략 | 메커니즘 | 모듈 | 데이터 |
|------|---------|------|--------|
| **Value Long (Buffett-style)** | rating ≥ +2 (저평가) 종목 매수, MoS ladder 진입 | `strategies/value_long.py` | valuation_scores + entry_plans |
| **Overvalued Short Filter** | rating ≤ -2 (고평가) 종목은 매수 금지 | `risk/exposure.py` 통합 | valuation_scores |
| **Pair Trade (Value vs Growth)** | 같은 섹터 저평가 매수 + 고평가 매도 | `strategies/value_pair.py` | composite fair value |
| **Crypto NVT Mean Reversion** | NVT > 90 percentile → 매도, < 10 → 매수 | `strategies/crypto_nvt.py` | crypto_valuation |
| **Earnings Quality Reversal** | DCF dispersion 큰 (확신 낮은) 종목 회피 | `risk/exposure.py` | dispersion_pct |

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

## 연구 원장 — 후보 라인 검증 기록

전략 breadth는 게이트 통과로만 획득된다. 모든 후보의 verdict(기각 포함)를 증거와 함께 추적 기록한다.
공통 프로토콜: IDEAL과 동일(핀 가격·106 유니버스·15×3y walk-forward·turnover 비용), verdict 바 사전 선언
(standalone = positive≥60% AND 평균초과>0 / diversifier = IDEAL 상관<0.7 AND 위기월>SPY).

| 라인 | 검증 | 결과 | verdict | 리포트 |
|------|------|------|---------|--------|
| **메가캡 TSMOM** (12-1 절대 모멘텀, 1/N+현금) | 2026-06-11 | positive 40%, 평균 초과 **−1.95%/yr** | **기각** — standalone 실패(불 마켓 현금 드래그); diversifier도 실패: IDEAL과 상관 **0.76**(≥0.7), 위기월 방어(−2.30 vs SPY −3.60%/mo)로 정당화 불가. trail_dd가 이미 같은 역할 | `scripts/tsmom_megacap_walkforward.py` → `out/tsmom-megacap-walkforward.md` |
| **메가캡 저변동성** (63d vol 랭크, top20 EW) | 2026-06-11 | positive 66.7%, 평균 초과 **−0.17%/yr**, Sharpe 1.29 | **standalone 기각**(평균 초과 음수 — 2023-25 AI 랠리 창 −12.45%가 결정적) / **DIVERSIFIER 후보 유지**: IDEAL과 상관 **0.53**, 위기월 −1.75 vs SPY −3.60%/mo. 슬리브로만 검토 — 결합 포트폴리오가 게이트 재통과 전 자금배분 금지 | `scripts/lowvol_megacap_walkforward.py` → `out/lowvol-megacap-walkforward.md` |
| **결합 IDEAL 80% + 저변동성 20%** (가중 사전선언, 그리드 금지) | 2026-06-11 | positive **100%(15/15)**, 평균 초과 **+6.50%/yr**, Sharpe **1.48**, worst MDD **16.61%** | **PASS** — 사전선언 3바 전부 충족: ①standalone ②Sharpe 1.48≥IDEAL 단독 1.41 ③worst MDD 16.61%≤19.19%. 트레이드: 초과 −1.65pp ↔ 일관성 13/15→15/15·꼬리 −2.6pp. 블렌드 수학 수치검증(공통월 기준 슬리브 사이 위치 확인, 단위테스트 5). **fee-stress PASS**: 5bps→100%/+6.22%/Sharpe 1.46, 10bps→93.3%/+5.77%/1.44·MDD 16.72%(fee-매칭 IDEAL@10bps 1.37/19.28% 대비로도 통과). **패밀리 PBO 완료**(11 configs=IDEAL 그리드8+TSMOM+저변+결합, 201 공통월): **PBO 0.330 FRAGILE**(IDEAL 자체 0.390 대비 근소 개선, 악화 없음)·결합이 rank 1/11·DSR@effN2 1.0000 — 부호 robust, 크기·선택 fragile = 배포후보와 동일 증거 수준. **→ 페이퍼 forward-OOS 단계 자격. 실자금 금지 유지**(IDEAL과 동일하게 페이퍼 원장 성숙이 유일한 신규 증거원) | `scripts/combined_ideal_lowvol_walkforward.py`·`scripts/breadth_family_pbo.py` → `out/combined-ideal-lowvol-walkforward{,-fee5,-fee10}.md`·`out/breadth-family-pbo.md` |
| **VIX 텀구조 시그널** (VIX/VIX3M>1 backwardation, `signals/vix_term.py`) | 2026-06-12, 정보 게이트(63d 블록 순열 5000회, 임계 사전선언·그리드 없음, 2010-2026 4136일) | 조건 321일(7.8%): 5d 초과 **+0.60%p(p=0.0024)** · 21d **+1.90%p(p=0.0056)** | **INFORMATIVE** — 사전선언 2바 통과(문헌 부호 일치: 패닉 평균회귀). 상태: **검증된 ADVISORY 레짐 플래그** — 자금 영향은 별도 전략 walk-forward 게이트 선행. 캐비앗: 유효표본=에피소드 수, 2008 미포함(^VIX3M 가용성), 크기 추정 거칢 | `scripts/vix_term_validation.py` → `out/vix-term-validation.md`, 핀 `vix-term-2026-06-12` |
| **VIX 피크-하락 시그널** (21d 피크≥30 & 현재≤피크×0.8, `signals/vix_peak.py`) | 2026-06-13, 동일 정보 게이트(핀 재사용 `vix-term-2026-06-12`, 상수 사전선언·그리드 없음) | 조건 469일(11.3%): 5d 초과 **+0.25%p(p=0.0356)** · 21d **+0.91%p(p=0.0420)** | **INFORMATIVE** — 2바 통과(vix_term보다 약함). 캐비앗: **vix_term과 에피소드 중복 — 독립 증거 아님**, 상수 2개는 연구자 선택. 검증된 ADVISORY 플래그, 자금 영향은 별도 게이트 | `scripts/vix_peak_validation.py` → `out/vix-peak-validation.md` |
| **COT 포지셔닝 극단** (S&P500 비상업 net, 156w COT지수≥90/≤10 contrarian, `signals/cot_extreme.py`) | 2026-06-13, 정보 게이트(COT 페치+핀 `cot-sp500-2024-12-31`, SPY 기존핀 재사용, 26w 블록순열, 임계 사전선언) | 극단 67주: contrarian-signed fwd 4w +0.02%p(p=0.48)·13w +0.79%p(p=0.36) | **NO EDGE** — 극단이 상시 contrarian tilt 대비 정보 무. (상시 contrarian 자체가 −0.76%/13w 손실 — 불마켓 추세페이딩.) **첫 비-VIX·직교 데이터 소스 시도**였으나 기각. n=67 소표본 캐비앗. 자금 배선 없음 | `data/ingest/cot_sp500.py`·`scripts/cot_validation.py` → `out/cot-validation.md` |
