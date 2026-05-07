# 미시경제 데이터

## 정의

이 시스템에서 "미시경제"는 거시(GDP/금리/인플레/환율)와 구분되는 **기업·산업 단위 데이터**:

1. **기업 펀더멘털** — 재무제표, 비율, 성장률
2. **공시/이벤트** — 어닝, 가이던스, 8-K, DART 공시
3. **인사이더/기관** — Form 4 (미국), DART 임원보고 (한국), 13F
4. **시장 구조** — 공매도, 대차, 거래원, 호가 흐름
5. **대안 데이터** — 검색 트렌드, 소셜 sentiment, 옵션 활동

## 데이터 소스 (전부 무료 또는 무료 티어)

### 미국

| 소스 | 데이터 | 한계 | 라이브러리 |
|------|--------|------|-----------|
| **SEC EDGAR** | 10-K/Q, 8-K, Form 4, 13F | 10 req/sec | `sec-edgar-downloader`, `edgar` |
| **yfinance** | 재무제표, 어닝, 애널리스트 컨센서스 | 비공식, 종종 변경 | `yfinance` |
| **FMP** | 어닝, 가이던스, 인사이더, DCF | 250 req/day 무료 | `fmpsdk` |
| **Alpha Vantage** | 펀더멘털 + 일부 거시 | 25 req/day | `alpha_vantage` |
| **Finviz** | 스크리너 + 인사이더 요약 | 스크래핑 (TOS 주의) | `finvizfinance` |

### 한국

| 소스 | 데이터 | 한계 | 라이브러리 |
|------|--------|------|-----------|
| **DART OpenAPI** | 사업보고서, 임원·주요주주 보고, 5% 보고 | 무료, key 신청 (즉시) | `OpenDartReader`, `dart-fss` |
| **pykrx** | 시총, PER, PBR, 외인 보유, 공매도 | 일봉/일별 단위 | `pykrx` (이미 사용) |
| **KRX 정보데이터** | 공매도, 대차잔고, 거래원 | 공식, 일부 일봉 | KRX API/스크래핑 |

### 크립토 (펀더멘털 개념 다름 → 온체인 데이터로 대체)

| 소스 | 데이터 | 한계 |
|------|--------|------|
| **Etherscan API** | 트랜잭션, 토큰 보유자, 가스 | 5 req/sec, 100k req/day 무료 |
| **CryptoQuant** | 거래소 입출금, 채굴자 행동 | 일부 무료 |
| **Glassnode** | 활성 주소, 코인 days destroyed | 무료 티어 매우 제한적 |
| **Dune Analytics** | SQL 기반 온체인 쿼리 | API는 유료지만 결과 export 무료 |
| **Defillama API** | DeFi TVL, 프로토콜 수익 | 완전 무료 |

### 산업/대안 데이터

| 소스 | 데이터 | 한계 |
|------|--------|------|
| **Google Trends** | 키워드 검색량 | `pytrends`, 비공식 |
| **FRED** | 거시 + 산업별 PMI/소매판매 | 무료, key 필요 |
| **BEA/BLS** | 미국 소비/노동 통계 | 무료 |
| **한국은행 ECOS** | 한국 경제통계 | 무료, key 필요 |
| **Reddit/Twitter** | 소셜 sentiment | 자체 스크래핑 (TOS 주의) |

## 알파 매핑

각 데이터가 어떤 전략에 어떻게 들어가는지:

| 데이터 | 알파 메커니즘 | 전략 모듈 |
|--------|-------------|-----------|
| ROE / FCF / EV / Asset Turnover | **Quality 팩터** (AQR) — 우량주가 장기 초과수익 | `strategies/factor_aqr.py` (확장) |
| 어닝 서프라이즈 + 다음 60일 수익률 | **PEAD** (Post-Earnings Announcement Drift) | `strategies/pead.py` |
| 애널리스트 가이던스 변경 | **Earnings Revision** 모멘텀 | `strategies/revisions.py` |
| Form 4 인사이더 클러스터 매수 | **Insider buying** 시그널 | `signals/insider.py` |
| 13F 신규 진입 / 비중 증가 | **Smart money following** (Pershing/Tiger 미러) | `signals/activist_13f.py` |
| 공매도 잔고 급증 | **Short squeeze 회피** + 빈 매도 신호 | `risk/short_interest.py` |
| Google Trends 키워드 (소비재) | **매출 nowcasting** | `signals/trends_nowcast.py` |
| 옵션 IV skew | **하방 위험 헤지** 신호 | `risk/option_skew.py` |
| 온체인 거래소 유입 (BTC) | **하방 압력** 시그널 | `signals/onchain_flows.py` |

## 권장 추가 순서

| Stage | 추가 항목 | 데이터 소스 | 핵심 효과 |
|-------|----------|------------|----------|
| **1.5** | 펀더멘털 (재무제표) | SEC EDGAR + DART + yfinance | Quality 팩터 즉시 활성, AQR 3-팩터 완성 |
| **2 확장** | 어닝 캘린더 + 서프라이즈 | FMP free + DART | PEAD 전략 추가 |
| **4** | Form 4 + 13F | SEC EDGAR | 액티비스트 시그널 (Pershing 모방) |
| **4** | 공매도 / 대차 | KRX + FINRA | 리스크 모니터링 |
| **5+** | 대안 데이터 (Trends, 소셜) | pytrends + 자체 수집 | 실험적, 백테스트 검증 후 |

## 디렉토리 확장

```
data/
├── ingest/
│   ├── alpaca_us.py              # 기존
│   ├── pykrx_kr.py               # 기존
│   ├── ccxt_crypto.py            # 기존
│   ├── edgar_us.py               # 신규: SEC EDGAR
│   ├── dart_kr.py                # 신규: DART OpenAPI
│   ├── fmp_earnings.py           # 신규: 어닝/가이던스
│   ├── trends_alt.py             # 신규: Google Trends
│   └── onchain_eth.py            # 신규: Etherscan
├── store/
│   ├── eod/                      # 기존
│   ├── intraday/                 # 기존
│   ├── fundamentals/             # 신규: 분기별 재무제표
│   ├── filings/                  # 신규: 공시 raw 텍스트
│   └── events/                   # 신규: 어닝/가이던스/Form 4
└── catalog.py                    # 확장: 펀더멘털 조회 API 추가
```

## DuckDB 스키마 확장

```sql
-- 분기 펀더멘털
CREATE TABLE fundamentals_q (
    symbol TEXT,
    market TEXT,
    period_end DATE,        -- 분기 말일
    fiscal_year INT,
    fiscal_q INT,
    revenue DOUBLE,
    operating_income DOUBLE,
    net_income DOUBLE,
    ebitda DOUBLE,
    fcf DOUBLE,
    total_assets DOUBLE,
    total_equity DOUBLE,
    total_debt DOUBLE,
    shares_out DOUBLE,
    source TEXT,
    asof TIMESTAMP,         -- 발표일 (point-in-time 보장)
    PRIMARY KEY (symbol, period_end)
);

-- 어닝 이벤트
CREATE TABLE earnings (
    symbol TEXT,
    announce_ts TIMESTAMP,  -- 발표 시각
    period_end DATE,
    eps_actual DOUBLE,
    eps_estimate DOUBLE,
    surprise_pct DOUBLE,
    revenue_actual DOUBLE,
    revenue_estimate DOUBLE,
    guidance_change TEXT
);

-- 인사이더 거래 (Form 4 / DART)
CREATE TABLE insider_trades (
    symbol TEXT,
    insider_name TEXT,
    insider_role TEXT,      -- CEO/CFO/Director/10%-owner
    txn_date DATE,
    txn_type TEXT,          -- buy/sell/option-exercise
    shares DOUBLE,
    price DOUBLE,
    value_usd DOUBLE,
    source TEXT             -- sec / dart
);

-- 13F 보유 변동
CREATE TABLE holdings_13f (
    fund_name TEXT,
    cik TEXT,
    quarter_end DATE,
    symbol TEXT,
    shares DOUBLE,
    value_usd DOUBLE,
    pct_of_portfolio DOUBLE,
    change_pct DOUBLE       -- 직전 분기 대비
);
```

## Point-in-time 데이터 (중요)

**가장 큰 함정**: 펀더멘털 데이터를 백테스트에서 그 시점에 알 수 없었던 정보로 사용 (look-ahead bias).

규칙:
- 분기 펀더멘털은 **발표일(asof) 이후**부터 사용 — period_end가 아님
- 어닝 발표 후 **1거래일 lag** 적용 (장중 발표는 다음날부터)
- 가이던스 변경도 발표일 기준
- 13F는 분기말 + **45일 보고 마감**까지 lag

`data/catalog.py`에 `as_of=` 파라미터 강제:
```python
df = catalog.get_fundamentals(symbol="AAPL", as_of="2024-03-15")
# → 2024-03-15 시점에 알 수 있었던 가장 최근 분기 데이터만 반환
```

## 비용 0원 도달 가능 범위

| 항목 | 무료 가능 |
|------|----------|
| 미국 펀더멘털 (분기) | ✓ SEC EDGAR 무한 |
| 한국 펀더멘털 (분기) | ✓ DART 무한 |
| 어닝 캘린더 + 컨센서스 | ✓ FMP 250 req/day (S&P500 절반 일일 갱신 가능) |
| 인사이더 거래 (미국) | ✓ SEC EDGAR Form 4 무한 |
| 13F | ✓ SEC EDGAR 무한 |
| 한국 임원 거래 | ✓ DART 무한 |
| 공매도 | ✓ KRX (한국) + FINRA (미국) 무료 |
| 옵션 데이터 | △ Yahoo로 일부, 정밀하려면 ORATS 유료 |
| 대안 데이터 (Trends) | ✓ pytrends 무료 |

다음 단계 유료 데이터 검토는 알파가 먼저 검증된 후에만 (Bloomberg $2k+/월, Refinitiv 등).

## 안티패턴

- **연간 보고서로 분기 백테스트** — 시간 분해능 안 맞음
- **현재 시점 데이터로 과거 백테스트** — look-ahead. 반드시 `asof` 컬럼
- **재무제표 발표일 무시** — 한국은 분기말 후 45일, 미국은 보통 4~6주 lag
- **한 데이터 소스만 신뢰** — yfinance는 재계산값. SEC EDGAR이 원본 진실
- **종목 코드 하드코딩** — 상장폐지/티커변경 추적 안 됨. 가급적 CIK(미국)/회사고유번호(한국) 기반
