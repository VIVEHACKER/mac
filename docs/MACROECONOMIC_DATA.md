# 거시경제 데이터

## 정의

미시(기업·산업 단위)와 구분되는 **국가/지역 경제 단위 데이터**:

1. **성장**: GDP, 산업생산, PMI, 소매판매
2. **물가**: CPI, PPI, GDP 디플레이터, 기대인플레이션
3. **금리/통화**: 정책금리, 국채금리(2Y/10Y), M2, 신용 스프레드
4. **노동**: 실업률, NFP(비농업고용), 신규실업청구
5. **외부**: 무역수지, 환율(DXY, 원달러), 외환보유액
6. **심리**: 소비자신뢰, 기업신뢰, ZEW, IFO, 한국 BSI/CSI

## 데이터 소스 (모두 무료)

### 미국/글로벌

| 소스 | 데이터 | 한계 | 라이브러리 |
|------|--------|------|-----------|
| **FRED** (https://fred.stlouisfed.org) | 800,000+ 시계열, vintage(ALFRED) 포함 | 무료 key, 120 req/min | `fredapi` (이미 추가됨) |
| **BEA** | GDP 상세, 개인소득, 산업GDP | 무료 key | `requests` 직접 호출 |
| **BLS** | CPI/고용/생산성 상세 | 일부 무료 | `requests` |
| **IMF / World Bank** | 글로벌 비교 (190+개국) | 완전 무료 | `wbdata`, `imfp` |

### 한국

| 소스 | 데이터 | 한계 |
|------|--------|------|
| **한국은행 ECOS API** (https://ecos.bok.or.kr/api/) | 한국 거시 모든 시계열 | 무료 key (즉시), 일일 호출 한도 있음 |
| **KOSIS** (통계청) | 사회/산업/인구 통계 | 일부 API 제공 |
| **금융감독원/금융결제원** | 가계신용, 카드 결제 | 공시 자료 수동 |
| **한국거래소 (KRX)** | 시장 통계, 외환 | 일부 무료 API |

### 발표 캘린더

| 소스 | 데이터 | 한계 |
|------|--------|------|
| **Trading Economics** | 글로벌 경제 캘린더 | 무료 키 가능 (제한적) |
| **Investing.com** | 캘린더 + 컨센서스 | 비공식 스크래핑 (`investpy`) |
| **FRED ALFRED** | 발표일/revision 이력 | 공식, 가장 신뢰 |

## 알파 매핑

| 데이터 | 알파 메커니즘 | 전략 모듈 |
|--------|-------------|-----------|
| 성장↑/↓ × 인플레↑/↓ 4사분면 | **Bridgewater All Weather** 자산 배분 | `strategies/risk_parity.py` (regime-aware 확장) |
| ISM PMI > 50 + 모멘텀 | **Risk-on/off 전환** | `strategies/regime_switch.py` |
| Yield curve inversion (10Y-2Y) | **침체 선행지표** — 방어자산 비중 ↑ | `signals/recession.py` |
| Real rate (실질금리 = 명목 - 기대인플레) | **금/원자재 가격 압력** | `strategies/risk_parity.py` |
| Surprise index (Citi/Bloomberg) | **매크로 모멘텀** | `strategies/macro_momentum.py` |
| CPI/FOMC 발표 surprise | **채권/달러 단기 이벤트** | `strategies/macro_event.py` |
| DXY / 원달러 환율 | **한국 수출주 노출** 헤지 | `risk/fx_exposure.py` |
| Term spread + Credit spread | **위험 prefer 단계** 식별 | `signals/risk_appetite.py` |

## 핵심 매크로 시계열 (수집 우선순위)

### 미국 (FRED ID)
| 시리즈 | FRED ID | 빈도 | 용도 |
|--------|---------|------|------|
| Federal Funds Rate | `DFF` | D | 정책금리 |
| 10Y Treasury | `DGS10` | D | 장기금리 |
| 2Y Treasury | `DGS2` | D | 단기금리 (yield curve) |
| 10Y - 2Y Spread | `T10Y2Y` | D | 침체 신호 |
| CPI | `CPIAUCSL` | M | 인플레이션 |
| Core PCE | `PCEPILFE` | M | Fed 선호 인플레 지표 |
| Unemployment | `UNRATE` | M | 노동시장 |
| Nonfarm Payrolls | `PAYEMS` | M | 고용 변동 |
| ISM Manufacturing | `MANEMP` (대용) | M | 제조업 활동 |
| Real GDP | `GDPC1` | Q | 성장 |
| M2 Money Supply | `M2SL` | M | 통화량 |
| DXY (Dollar Index) | `DTWEXBGS` | D | 달러 강도 |
| VIX | `VIXCLS` | D | 변동성 |

### 한국 (ECOS 통계표코드 — 일부 예시, 정확한 코드는 ECOS 사이트에서 검색)
| 시리즈 | ECOS code | 빈도 | 용도 |
|--------|-----------|------|------|
| 한국은행 기준금리 | `722Y001` | D/M | 정책금리 |
| 국고채 3Y/10Y | `817Y002` | D | 장기금리 |
| 소비자물가지수 (CPI) | `901Y009` | M | 인플레이션 |
| 실업률 | `901Y027` | M | 노동시장 |
| 산업생산지수 | `901Y033` | M | 성장 |
| 원/달러 환율 | `731Y001` | D | 환율 |
| 경상수지 | `301Y013` | M | 외부 |
| 가계신용 | `151Y005` | Q | 부채 |

(정확한 ECOS 코드는 사용 시점에 https://ecos.bok.or.kr 에서 검증)

## DuckDB 스키마

```sql
-- 시계열 데이터
CREATE TABLE macro_series (
    series_id TEXT,             -- FRED ID 또는 ECOS code
    country TEXT,               -- US/KR/EU/CN/JP/GLOBAL
    series_name TEXT,
    category TEXT,              -- growth/inflation/rates/labor/trade/fx/sentiment
    freq TEXT,                  -- D/W/M/Q/Y
    unit TEXT,                  -- pct/index/value/yoy_pct
    asof_date DATE,             -- 데이터 시점 (예: 2024-Q3)
    release_ts TIMESTAMP,       -- 발표 시각 (PIT 보장)
    value DOUBLE,
    revision_n INT,             -- 0=최초, 1=1차 수정, ...
    source TEXT,
    PRIMARY KEY (series_id, asof_date, revision_n)
);

-- 발표 캘린더 + 서프라이즈
CREATE TABLE macro_releases (
    release_id TEXT PRIMARY KEY,
    series_id TEXT,
    release_ts TIMESTAMP,       -- 발표 시각 (분 단위)
    asof_date DATE,             -- 어떤 시점 데이터인지
    actual DOUBLE,
    consensus DOUBLE,
    surprise DOUBLE,            -- z-score: (actual - consensus) / std
    importance TEXT,            -- high/medium/low
    country TEXT
);

-- 경제 regime 분류 (Bridgewater 4사분면)
CREATE TABLE macro_regime (
    asof_date DATE PRIMARY KEY,
    growth_yoy DOUBLE,          -- GDP YoY
    inflation_yoy DOUBLE,       -- CPI YoY
    growth_state TEXT,          -- rising / falling
    inflation_state TEXT,       -- rising / falling
    quadrant INT,               -- 1=성장↑인플레↑, 2=성장↑인플레↓, 3=성장↓인플레↑, 4=성장↓인플레↓
    confidence DOUBLE           -- 분류 확신도
);
```

## Point-in-time 매크로 (look-ahead 방지)

매크로는 미시보다 더 까다롭다 — **revision이 빈번**하고 **발표 시각이 분 단위로 중요**.

### 규칙
1. **vintage 데이터 사용** — FRED ALFRED API로 발표 시점 값 조회
2. **release_ts 이전 데이터 사용 금지** — 분기 GDP는 분기말 + 1개월 lag
3. **CPI/PMI/PPI는 발표 시각 +1초부터 사용** (장중 발표)
4. **잠정/확정 revision 추적** — 한국은 보통 3차 수정까지

### catalog.py 인터페이스
```python
df = catalog.get_macro(
    series_id="DGS10",
    as_of="2024-03-15",         # 이 시점에 알 수 있던 값
    use_vintage=True,           # FRED ALFRED 사용
)
```

## 권장 추가 순서

| Stage | 추가 항목 | 데이터 소스 | 핵심 효과 |
|-------|----------|------------|----------|
| **1.6** (Stage 1.5 직후) | 핵심 매크로 시리즈 수집 + DuckDB | FRED (15개) + ECOS (8개) | regime 분류 가능 |
| **2 확장** | Risk parity에 regime 통합 | FRED yield curve + CPI | Bridgewater 4사분면 |
| **4 확장** | Macro momentum + recession signal | FRED + Surprise index | 위험자산 비중 동적 |
| **4 확장** | FX 노출 모니터 | DXY/원달러 | 한국 수출주 헤지 |
| **5+** | Macro event trading (CPI/FOMC) | 발표 캘린더 + 컨센서스 | 단기 채권/통화 |

## 비용 0원 도달 가능 범위

| 항목 | 무료 가능 |
|------|----------|
| 미국 매크로 (FRED) | ✓ 800,000+ 시계열 무한 |
| 한국 매크로 (ECOS) | ✓ 무한 (일일 호출 한도 내) |
| 글로벌 비교 (IMF/WB) | ✓ 무한 |
| Vintage data (ALFRED) | ✓ FRED 무료 |
| 발표 캘린더 | △ Trading Economics 일부 무료, investpy 비공식 |
| Surprise index | ✗ Citi/Bloomberg 유료 — 자체 계산 가능 (actual-consensus 직접 수집) |

## 디렉토리 확장

```
data/
├── ingest/
│   ├── ...                      # 기존
│   ├── fred_macro.py            # 신규: FRED + ALFRED vintage
│   ├── ecos_kr.py               # 신규: 한국은행 ECOS REST
│   └── macro_calendar.py        # 신규: 발표 캘린더 + surprise 계산
└── store/
    └── macro/                   # 신규: 매크로 시계열 + regime
```

## 한국은행 ECOS 호출 패턴

ECOS는 별도 라이브러리 없이 `requests`로 호출:

```python
import os, requests

key = os.getenv("ECOS_API_KEY")
url = f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/1000/722Y001/D/20100101/20251231"
data = requests.get(url, timeout=30).json()
rows = data["StatisticSearch"]["row"]
```

응답 키가 한국어이므로 `data/ingest/ecos_kr.py`에서 정규화 매핑 필수.

## 안티패턴

- **현재 revision으로 과거 백테스트** — vintage 무시 → look-ahead. 반드시 ALFRED
- **발표일만 알고 발표시각 모름** — 장중 CPI 발표는 14:30 ET, 그 이전 데이터로 거래하면 미래정보
- **거시 단독 시그널로 종목 선택** — 거시는 자산배분/regime용, 종목은 미시
- **분기 GDP 잠정값으로 즉시 거래** — 보통 1개월 lag + 두 번 더 revision
- **다른 나라 매크로를 즉시 적용** — 시간대/통화/구조 차이. 정규화 후 비교
- **명목 금리만 보고 실질 평가** — 인플레 빼서 실질금리(real rate) 사용
