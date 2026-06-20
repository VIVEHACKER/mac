# Valuation & Entry — 적정가, 고/저평가, 진입가 추천

데이터를 모으는 데서 끝내지 않고 **"얼마면 비싼가 / 얼마면 싼가 / 얼마에 진입하나"** 까지 자동 도출.

## 세 가지 출력

| 출력 | 의미 | 산출물 |
|------|------|--------|
| **Fair Value** | 펀더멘털 기반 적정 주가 (point-in-time) | 단일 가격 + 신뢰구간 (e.g. "$170 ± $25") |
| **Valuation Score** | 시가 vs 적정가 z-score (-3 ~ +3) | 매도(+) / 매수(−) 강도 |
| **Entry Plan** | 분할 진입 가격대 + 비중 | Limit ladder (e.g. "30% @ $145, 40% @ $138, 30% @ $128") |

## 평가 방법 (주식)

세 방법을 병렬 계산 → 가중 평균 → 단일 fair value + dispersion(신뢰도).

### 1. DCF (Discounted Cash Flow)

```
FCF₁..ₙ projections (5~10년)
  ├─ 과거 5년 FCF 성장률 → 향후 5년 fade to GDP growth
  ├─ WACC = Risk-free + Beta × ERP + Cost of Debt × (1-tax) × D/(D+E)
  └─ Terminal Value = FCFₙ × (1+g) / (WACC - g)

Equity Value = Σ FCFₜ / (1+WACC)ᵗ + TV / (1+WACC)ⁿ - Net Debt - Minority Interest
Fair Price = Equity Value / Shares Outstanding

민감도: WACC ±1%, g ±1% → 3×3 매트릭스로 신뢰구간
```

데이터: SEC EDGAR / DART 재무제표 + FRED 무위험금리 + 베타 자체 계산.

### 2. Relative Multiples (Peer Comparison)

```
Peer 그룹 정의:
  GICS Sector + Industry + 시가총액 ±50% (5~15개 동종 종목)

Median Multiple × Subject's metric:
  P/E:        median(peer P/E) × forward EPS
  EV/EBITDA:  median(peer EV/EBITDA) × forward EBITDA - Net Debt
  P/B:        median(peer P/B) × Book Value
  P/Sales:    median(peer P/S) × Revenue
```

데이터: SEC EDGAR / DART + 시장가격.

### 3. Residual Income Model (RIM)

```
RI = Net Income - (Cost of Equity × Beginning Book Value)

Equity Value = Book Value₀ + Σ RIₜ / (1+rₑ)ᵗ + Terminal RI

은행/금융주에 강함 (FCF 추정 어려운 산업)
```

### Composite Fair Value

```python
# 가중치 (산업별 조정)
weights = {
    'tech':       {'dcf': 0.5, 'multiple': 0.4, 'rim': 0.1},
    'financial':  {'dcf': 0.2, 'multiple': 0.3, 'rim': 0.5},
    'industrial': {'dcf': 0.4, 'multiple': 0.5, 'rim': 0.1},
    'consumer':   {'dcf': 0.3, 'multiple': 0.6, 'rim': 0.1},
    'reit':       {'dcf': 0.2, 'multiple': 0.7, 'rim': 0.1},  # NAV 가까움
}

fair = w_dcf*FV_dcf + w_mult*FV_mult + w_rim*FV_rim
dispersion = std([FV_dcf, FV_mult, FV_rim]) / fair  # 낮을수록 신뢰
```

## 평가 방법 (크립토)

DCF/Multiples 직접 적용 불가. 온체인 + 상대비교.

| 지표 | 의미 | 활용 |
|------|------|------|
| **NVT (Network Value/Tx)** | 주식 P/E 비유 (시총 ÷ 일일 거래량) | NVT 90 percentile = 고평가 |
| **MVRV** | 시가총액 ÷ realized cap (보유자 평균 매수가) | MVRV > 3.5 거의 항상 정점 |
| **Stock-to-Flow (BTC)** | 채굴량/유통량 모델 (S2F) | 장기 fair value 트렌드 |
| **DeFi PE** | 프로토콜 시가 ÷ 연환산 수익 | DefiLlama 데이터 |
| **Funding Rate 평균** | 8h funding 평균 → 시장 과열도 | DERIVATIVES_DATA.md |

크립토 fair value는 단일값보단 **range** + **historical percentile**로 표현.

## Valuation Score

```
score = (fair - current) / current × 100   # 단순 % 할인
또는
z = (current_PE - peer_PE_history_mean) / peer_PE_history_std

score 매핑:
  z > +2     → 매우 고평가 (-3, 매도)
  z > +1     → 고평가 (-2)
  z > +0.5   → 약간 고평가 (-1)
  -0.5~+0.5  → 적정 (0)
  z < -0.5   → 약간 저평가 (+1)
  z < -1     → 저평가 (+2)
  z < -2     → 매우 저평가 (+3, 매수)
```

**신뢰도 모디파이어**:
- DCF/Multiple/RIM dispersion < 15% → 신뢰도 high
- 어닝 서프라이즈 직후 30일 이내 → 신뢰도 medium (가이던스 변동)
- 적자 기업 → DCF 신뢰도 low → multiple/relative 위주

## Entry Plan — 진입 가격 추천

Fair value 알아도 그 가격에 시장 도달한다는 보장 없음. **분할 매수 ladder** 권장.

### 1. Margin of Safety (Buffett-style)

```
target_entry = fair × (1 - mos)
  mos = 20% (안정 우량주) ~ 40% (사이클/적자 회복주)
```

### 2. Technical 보강

```
support_levels = [
    20일 저점,
    fibonacci 0.382 / 0.5 / 0.618 retrace,
    200일 이동평균 (장기 지지),
    VWAP (anchored to last earnings),
]
```

### 3. Volatility 조정

```
ATR_pct = ATR(20) / price
entry_band = [current × (1 - k*ATR_pct) for k in [1, 2, 3]]
```

### 4. Ladder 합성

```python
def make_ladder(symbol, fair, current, atr_pct, mos=0.25):
    target = fair * (1 - mos)
    
    # 3단 ladder
    return [
        {'price': current * (1 - 1*atr_pct), 'weight': 0.3, 'reason': 'first dip'},
        {'price': current * (1 - 2*atr_pct), 'weight': 0.4, 'reason': 'volatility-adj'},
        {'price': max(target, current * (1 - 3*atr_pct)), 'weight': 0.3, 'reason': 'mos floor'},
    ]
```

출력 예시:
```
AAPL 적정가: $170 (DCF $175 / Multi $168 / RIM $167, σ=8%)
현재: $158 → score: +0.5 (약간 저평가)

추천 진입 (총 자본의 5%):
  30% @ $156 (-1.3%)  — first dip
  40% @ $152 (-3.8%)  — vol-adj
  30% @ $145 (-8.2%)  — MoS floor

손절: $138 (-12.7%, MoS의 -5%)
목표: $200 (DCF + 15%, 6~12개월)
```

## 단일종목 추천기 + 전 유니버스 스캔 — `evaluate_ticker` / `scan_universe` (P1-P2)

티커 검색 → 자동 평가/추천. **핵심 원칙: 추천의 신호는 새 휴리스틱이 아니라 검증된
IDEAL 라인이 실제로 쓰는 신호(`strategies.factor_aqr.rank_aqr_factors`)를 그대로
재활용**한다. 이 신호는 본질적으로 전 유니버스 횡단면 Z-score이므로, 단일종목 평가 =
"전 유니버스를 랭킹한 뒤 그 종목 + 백분위를 잘라내기"와 동일한 연산이다 (몇 개만 보는
것은 의미 없다는 요구를 구조적으로 강제).

파이프라인 (`valuation/recommendation.py:evaluate_ticker`):
1. `aqr_rank_for(ticker, universe)` → rank / percentile / in_top_n (검증된 AQR 신호)
2. `composite_fair_value(DCF, multiples)` → 적정가 + dispersion
3. `make_entry_plan(...)` → 평균매수단가(target_entry) + 손절 + 익절 + R/R
4. `valuation/confidence.py:calibrated_confidence(...)` → 0-100 신뢰도

신뢰도 = 신호강도(percentile) × 전략 신뢰도(WF positive rate × PSR × DSR) × 적정가
신뢰도. **지어내지 않고** 검증 통계로 캘리브레이션한다. 검증 유니버스 밖 종목은
forward edge가 없으므로 신뢰도를 하드 캡(기본 25)하고 AVOID로 처리.

**액션은 검증된 신호(AQR 랭크)가 결정**한다 — 신뢰도와는 별개 축: `BUY`(in_top_n =
전략이 실제 보유하는 종목, 신뢰도 medium↑) / `HOLD`(in_top_n인데 신뢰도 low, 또는
top-N 밖이지만 medium↑) / `AVOID`(그 외 + 유니버스 밖). 신뢰도 band는 "얼마나 확신/사이즈"
를 나타내지 BUY/HOLD를 뒤집지 않는다(검증된 전략의 결정을 DCF 오버레이가 거부하지 못하게).

### 진입은 항상 변동성 밴드 (모멘텀 전략이므로) — DCF는 맥락일 뿐
검증된 edge는 **모멘텀**이지 가치회귀가 아니다. 따라서 진입래더는 **항상 현재가 기준
변동성(ATR) 밴드**(분할 매수 = 눌림 매수)이고, DCF 적정가는 "싸다/비싸다" **맥락**으로만
표시한다 — 진입/타겟을 몰지 않는다. 이유: 순진한 단일단계 DCF는 고성장 메가캡을 체계적
과소평가하고(AAPL $88 vs $311), 모멘텀 승자는 보통 적정가 위에서 거래되므로 적정가를
앵커로 쓰면 익절가가 현재가보다 낮게 나오는 무의미한 출력이 된다(실제로 TGT/HD/PG에서
발생 → 수정). target_exit = 현재가 +15%(일반 모멘텀 타겟, 적정가 타겟 아님), 실제 청산은
트레일링 스톱(-10%→0.5x) + 21일 리밸런싱. DCF 신뢰성(시장가 [0.5x,2.0x] 내)은 신뢰도
*점수*에만 반영(맥락 라벨), 진입래더에는 무영향.

### 전 유니버스 스캔 — `scan_universe` (P2)
AQR 신호가 횡단면이라 **한 번의 패스로 검증 유니버스(106종) 전체를 랭킹**하고 각 종목에
진입밴드·신뢰도를 부착해 정렬된 표를 낸다("몇 개만은 의미 없다"의 정직한 답). top-N(★)이
전략의 실제 매수 후보. **검증 유니버스 밖(소형주 등) 스캔은 안 한다** — P5 forward 검증에서
펀더멘털 funnel은 forward edge 없음(IC≈0)으로 판명되어, 거기에 자동추천을 붙이면 노이즈
증폭이기 때문(async/rate-limit 인프라가 불필요한 이유이기도 하다).

### 설정 — `config/validated_strategies.json`
추천기의 앵커 전략과 신뢰도 통계(wf_positive_rate/psr/dsr)는 이 파일에서 로드한다.
`aqr_top7_cap20_trail10`(gate-approved IDEAL 베이스라인)이 기본값. 현재 값은 pinned
walk-forward + PBO 산출물 기준이다: 10bps 비용 반영 평균 SPY 초과 +7.40%/yr,
positive 86.7%, baseline PSR/DSR 1.00. 단 PBO 0.390으로 **수익률 크기/설정 선택은
fragile**하므로 추천기는 신뢰도 calibration에만 쓰고, 실자금 투입은 paper OOS ledger가
성숙한 뒤 별도 승인한다.

### 실행
```bash
python -m scripts.evaluate_ticker AAPL          # 단일종목, pinned snapshot 최신일
python -m scripts.evaluate_ticker NVDA --asof 2026-05-30
python -m scripts.scan_universe --top 20        # 전 유니버스 랭킹 표
python -m scripts.scan_universe --output out/universe-scan.md
```
검증된 IDEAL 라인과 동일한 pinned 스냅샷(`data/snapshots/prices-ideal-*.csv` +
`fundamentals-*-gp.csv`)을 읽어 재현 가능.

### 대시보드 노출 — 종목선정 탭
`dashboard/app.py`의 **종목선정** 탭 상단 "🎯 검증 선정" 패널이 동일한 `scan_universe`
(핀드 스냅샷)를 호출해 106종 랭킹과 top-N(★) 실제 매수 후보를 액션·신뢰도·진입/손절/목표와
함께 표시한다(CLI `python -m scripts.scan_universe`와 결과 일치, `st.cache_data` 캐시 +
새로고침 + 전체 랭킹 expander). 같은 탭 하단 "커스텀 유니버스 랭킹"은 탐색용 라이브 랭커로,
US에서 입력을 비우면 검증 유니버스(106) 전체를 채운다(이전의 메가캡 8종 하드코딩 기본값 제거).
추천기 탭의 횡단면 컨텍스트도 빈칸이면 동일한 106 풀을 써 랭크·신뢰도가 `evaluate_ticker`와
정합한다.

## DuckDB 스키마

```sql
-- 기업별 적정가 시계열
CREATE TABLE valuations (
    symbol TEXT,
    asof_date DATE,             -- 평가 기준일 (분기 펀더멘털 발표 후)
    fair_value DOUBLE,          -- 가중 평균
    fair_dcf DOUBLE,
    fair_multiple DOUBLE,
    fair_rim DOUBLE,
    dispersion_pct DOUBLE,      -- 세 모델 표준편차 / 평균
    confidence TEXT,            -- high / medium / low
    
    -- 입력값 기록 (재현성)
    fcf_growth_5y DOUBLE,
    wacc DOUBLE,
    terminal_g DOUBLE,
    peer_pe_median DOUBLE,
    peer_count INT,
    
    asof_ts TIMESTAMP,
    PRIMARY KEY (symbol, asof_date)
);

-- 평가 점수 (일별, 시장가와 결합)
CREATE TABLE valuation_scores (
    symbol TEXT,
    date DATE,
    current_price DOUBLE,
    fair_value DOUBLE,
    discount_pct DOUBLE,        -- (fair - current) / current
    z_score DOUBLE,
    rating INT,                 -- -3 ~ +3
    confidence TEXT,
    PRIMARY KEY (symbol, date)
);

-- 진입 추천 (현재 시점)
CREATE TABLE entry_plans (
    symbol TEXT,
    asof_ts TIMESTAMP,
    current_price DOUBLE,
    fair_value DOUBLE,
    target_entry DOUBLE,        -- MoS 적용가
    stop_loss DOUBLE,
    target_exit DOUBLE,         -- fair × upside_factor
    
    ladder_json TEXT,           -- [{price, weight, reason}, ...]
    
    risk_reward DOUBLE,         -- (exit - entry) / (entry - stop)
    expected_holding_days INT,
    PRIMARY KEY (symbol, asof_ts)
);

-- Peer 그룹 정의 (재계산 캐시)
CREATE TABLE peer_groups (
    symbol TEXT,
    asof_date DATE,
    sector TEXT,
    industry TEXT,
    peer_symbols TEXT,          -- JSON array
    PRIMARY KEY (symbol, asof_date)
);
```

## 디렉토리 확장

```
valuation/                       # 신규 — 적정가/평가/진입 모듈
├── _base.py                     # Valuator 추상 클래스
├── dcf.py                       # DCF 계산 + 민감도
├── multiples.py                 # Peer median 기반 상대 평가
├── rim.py                       # Residual Income Model
├── crypto_valuation.py          # NVT/MVRV/S2F
├── peer_groups.py               # GICS + 시총 기반 peer 선택
├── composite.py                 # 세 방법 통합 fair value
├── score.py                     # z-score + rating
└── entry.py                     # MoS + technical + ATR ladder
```

## 시스템 통합

```
Stage 1.5 펀더멘털 데이터
        ↓
valuation/ 모듈이 분기마다 자동 재계산 (어닝 발표 후 24h)
        ↓
DuckDB valuations 테이블 갱신
        ↓
일일 valuation_scores 갱신 (시장가 변동 따라)
        ↓
대시보드: 보유 종목 + 관심 종목 모두 표시
        ↓
strategies/factor_aqr.py가 valuation_scores 활용 (Value 팩터 강화)
strategies/value_long.py 신설: rating ≥ +2 종목 매수
risk/exposure.py: rating ≤ -2 종목 비중 제한
```

## Point-in-time 보장

- **DCF 계산**: 발표된 재무제표만 (Stage 1.5의 PIT 카탈로그 사용)
- **Peer median**: 평가 기준일 시점에 알 수 있던 peer 가격만
- **WACC**: FRED yield 시계열 사용 (vintage)
- **백테스트 시 valuation 재현**: `as_of=` 파라미터로 과거 fair value 재계산

## 안티패턴

- **단일 P/E로 매수 결정** — peer/historical 비교 없으면 의미 없음. 한 종목 P/E 30이 비싼지 싼지 알 수 없다
- **DCF 가정값 미공개** — fair value $170만 보여주고 WACC, growth 안 보여주면 신뢰 불가. dispersion + 입력값 기록 필수
- **Fair value를 진입가로** — 적정가에 도달할 거란 보장 없음. MoS + ladder 필수
- **단일 시점 평가** — 어닝 발표 후 가이던스 변동으로 fair 크게 변동. 분기마다 재계산 + dispersion 표시
- **크립토에 DCF 적용** — 현금흐름 없는 자산에 부적절. NVT/MVRV/상대 percentile 사용
- **Peer 선택을 GICS만으로** — 시총·성장률·마진 differ하면 multi 비교 의미 없음. 시총 ±50% + 성장률 비슷한 5~15개
- **z-score 절대값으로 트레이딩** — 산업/시장별로 평균이 다름. cross-sectional ranking 권장

## 권장 추가 위치 — Stage 2.5

Stage 2 (AQR + PEAD 백테스트) 직후, Stage 3 (페이퍼 트레이딩) 전.
이유: Stage 2에서 검증된 알파를 valuation으로 강화 → 진입 가격 결정 → Stage 3 페이퍼.

## 비용

전부 **무료**. 추가 데이터 소스 불필요 — Stage 1.5 (펀더멘털) + Stage 1 (가격) + Stage 1.6 (FRED 무위험금리) 데이터만으로 가능.
