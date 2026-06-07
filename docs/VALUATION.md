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

---

## 코어 바스켓 (펀드 장기 슬리브 ~35% durable anchor)

`engine/core_basket.py`는 컴파운더 스크린을 실제 장기 슬리브(50/50 바벨의 ~35% durable anchor)로
구성하는 **순수 선택 엔진**이다 — 적정가 산출이 아니라 **포트폴리오 구성**이라 정본 문서는 운영
런북에 있다. 밸류(저 ps/pb) + GP/assets **백분위 틸트**(z-score 아님), 섹터당 상한 + 8% 하드캡,
thesis-hold 리밸런서, 팩터 알파 주장 없음. 전체 선택/랭크/리밸런스/드라이버/검증 상세:

→ **`docs/COMPOUNDER_OPERATIONS.md` — "코어 바스켓 (Core Basket)" 섹션** 참조.

---

## 헌트 바스켓 (펀드 비대칭 상방 슬리브 ~15%)

`engine/hunt_basket.py`는 코어 바스켓의 짝 — 비대칭 상방 슬리브(50/50 바벨의 ~15%)를 구성하는
**순수 후보-발굴 엔진**. 코어가 durable 닻이라면 헌트는 사용자 재량 확신을 시스템 가드로 감싼다.

**정직한 역할**: 알파 = **사용자 재량 확신**(트랙레코드 8/10), 시스템 = ① 신호 이벤트로 후보 surface
(컨빅션+kill-thesis+플래그) ② 생존 가드 강제(작은 사이징·종목당 캡·분산·레버리지0). **자동매수 아님 —
최종 픽은 사용자.** validate-before-trust: 6신호 중 가중 자격 통과 신호 0개라 **점수 블렌드 금지.**

**선택 로직** (검증 코드·signals/* import 재사용, 무손상):
1. **SCREEN** (`_signal_eligible`, 가벼움): insider 매수 이벤트만 게이트. **코어와 반대로** 고부채·
   희석·적자 종목을 제외 안 함(플래그로만) — 위험은 *사이징으로* 관리(헌트의 핵심 반전).
2. **RANK** (`_rank_candidates`): insider 컨빅션(달러가중, 유일 suggestive IC) 단독. net_issuance
   (기각)·foreign_flow(미검증)는 **서술 플래그만, 점수 블렌드 아님**. cheapness 백분위 = 약한 타이브레이커.
3. **SIZE** (`select_hunt_basket`): 작은 등가중 1/n + 종목당 캡, **Kelly 없음**(가짜 엣지 입력 회피).
   슬리브-상대 가중 + `sleeve_fraction`로 **펀드-레벨 생존 수치 명시**(기본값 target_n=6·max_per_name=0.40·
   sleeve_fraction=0.15 기준: 단일종목0→펀드 −2.5%, 슬리브 전멸→−15%; n<target_n이거나 캡 발동 시 더
   작아짐 — 실제 수치는 `format_hunt_basket`이 매 실행 동적 출력).
4. **kill-thesis** (`_kill_thesis`): 펀더멘털(신호역전+distress), **가격 손절 없음(0컷)**.

**스모크 검증**(as_of=2023-06-30): 963 유니버스 → 150 signal-eligible → top 6, 각 2.5% 펀드.
SWX/GME/RCUS 같은 distress+내부자매수 종목이 **유지+플래그+kill-thesis**로 작은 사이즈로 편입(코어면
제외됐을 것) — 반전 동작 확인. **13 테스트, ruff/mypy 클린.**

**드라이버**: `python -m scripts.hunt_basket [--as-of YYYY-MM-DD] [--target-n 6] [--max-per-name 0.40]
[--sleeve-fraction 0.15]`. 카탈로그 insider_trades(`get_insider_trades`, PIT) + 핀 펀더/가격에서
insider/net_issuance 신호 생성 → `select_hunt_basket` → `format_hunt_basket`.

**deferred**: 전진 적중률 원장(사용자 엣지 실측, paper_oos 패턴), thesis-hold 리밸런서,
foreign_flow IC 하니스, 라이브 주문 배선.
