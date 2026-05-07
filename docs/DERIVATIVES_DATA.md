# Derivatives & Microstructure Data

크립토 perp 선물의 마이크로구조(funding/OI/long-short)와 옵션 시장 sentiment(VIX/skew/put-call). 가격 데이터로는 보이지 않는 **포지션 쏠림과 변동성 기대**를 추적.

## 데이터 소스

### Crypto 마이크로구조 (전부 무료, CCXT)

| 데이터 | 소스 | API | 빈도 |
|--------|------|-----|------|
| **Funding Rate** | Binance/Bybit/OKX perp | `fetch_funding_rate_history` | 8시간 정산 (거래소별) |
| **Open Interest** | 동일 | `fetch_open_interest_history` | 5분~1시간 |
| **Long/Short Ratio** | Binance/Bybit | 거래소별 엔드포인트 (CCXT proxy) | 5분/15분/1시간/일 |
| **Liquidation** | Binance/Bybit WebSocket | CCXT pro 또는 raw WS | 실시간 |
| **DVOL (BTC IV)** | Deribit | `https://www.deribit.com/api/v2/public/get_volatility_index_data` | 1분~일 |

### 옵션 sentiment

| 데이터 | 소스 | 접근 |
|--------|------|------|
| **VIX (30일 S&P500 IV)** | CBOE | https://www.cboe.com/tradable_products/vix/vix_historical_data/ — CSV 무료 |
| **VIX9D / VIX3M / VIX6M** | CBOE | term structure 비교용 |
| **CBOE SKEW Index** | CBOE | 꼬리위험 (>140 = 위험 회피 강화) |
| **Put/Call Ratio (CPCE / CPCI)** | CBOE | equity / index 별도 |
| **종목 옵션 체인** | Yahoo Finance | `yfinance.Ticker(sym).option_chain(expiry)` |
| **BTC/ETH 옵션** | Deribit | 무료 API, IV smile + term |
| **KOSPI200 옵션 + V-KOSPI** | KRX | pykrx 일부, 또는 KRX 정보데이터시스템 |

## 알파 매핑

### Crypto 마이크로구조

| 데이터 | 알파 메커니즘 | 모듈 |
|--------|-------------|------|
| **Funding rate 양수 극단** (>0.05% per 8h) | 롱 과열 → 청산 위험 → 매도 신호 | `signals/funding_extreme.py` |
| **Funding rate 음수** | 숏 과열 → 숏 스퀴즈 가능성 | 동일 |
| **OI 급증 + 가격 횡보** | 변동성 확장 임박 (방향 미정) | `signals/oi_squeeze.py` |
| **OI 하락 + 가격 상승** | 약한 추세 (숏 커버) → 추세 의심 | `signals/oi_trend.py` |
| **Long/Short 극단** (>3 또는 <0.33) | mean reversion | `signals/ls_ratio.py` |
| **Funding-Spot 차익** | 무위험 차익 (펀딩 받으며 spot 매수) | `strategies/funding_arb.py` |

### 옵션 sentiment

| 데이터 | 알파 메커니즘 | 모듈 |
|--------|-------------|------|
| **VIX/VIX3M < 0.85** (강한 백워데이션) | 단기 공포 극단 → 단기 반등 | `signals/vix_term.py` |
| **VIX > 30 + 하락 시작** | 변동성 정점 → 위험자산 진입 | `signals/vix_peak.py` |
| **Put/Call > 1.2** (극단 비관) | 단기 contrarian 매수 | `signals/put_call_extreme.py` |
| **SKEW > 140** | 꼬리위험 가격에 반영 → 헤지 비싸짐 | `risk/tail_risk.py` |
| **종목 IV 급등 + 어닝 임박** | 어닝 후 IV crush 매도 전략 | `strategies/iv_crush.py` |
| **DVOL (BTC IV) 극단** | 크립토 변동성 mean reversion | `signals/btc_vol.py` |

## DuckDB 스키마

```sql
-- Crypto perp 마이크로구조
CREATE TABLE crypto_funding (
    exchange TEXT,
    symbol TEXT,                 -- BTCUSDT-PERP, ETHUSDT-PERP
    ts TIMESTAMP,
    funding_rate DOUBLE,         -- 8시간 펀딩률 (decimal, e.g. 0.0001 = 0.01%)
    next_funding_ts TIMESTAMP,
    mark_price DOUBLE,
    PRIMARY KEY (exchange, symbol, ts)
);

CREATE TABLE crypto_oi (
    exchange TEXT,
    symbol TEXT,
    ts TIMESTAMP,
    open_interest DOUBLE,        -- contracts
    open_interest_value DOUBLE,  -- USD notional
    PRIMARY KEY (exchange, symbol, ts)
);

CREATE TABLE crypto_long_short (
    exchange TEXT,
    symbol TEXT,
    ts TIMESTAMP,
    period TEXT,                 -- 5m/15m/1h/1d
    long_short_ratio DOUBLE,
    long_account_pct DOUBLE,
    short_account_pct DOUBLE,
    PRIMARY KEY (exchange, symbol, ts, period)
);

CREATE TABLE crypto_liquidations (
    exchange TEXT,
    symbol TEXT,
    ts TIMESTAMP,
    side TEXT,                   -- long_liq / short_liq
    qty DOUBLE,
    price DOUBLE,
    notional DOUBLE
);

-- 옵션 sentiment (지수 단위)
CREATE TABLE option_sentiment (
    date DATE,
    market TEXT,                 -- US / KR / CRYPTO
    vix DOUBLE,                  -- 30일 IV
    vix_short DOUBLE,            -- VIX9D 또는 V-KOSPI 단기
    vix_3m DOUBLE,
    vix_6m DOUBLE,
    skew DOUBLE,                 -- CBOE SKEW
    put_call_equity DOUBLE,
    put_call_index DOUBLE,
    dvol DOUBLE,                 -- Deribit BTC vol index (CRYPTO market만)
    PRIMARY KEY (date, market)
);

-- 종목별 옵션 체인 (선택, 데이터 무거움 — 대형주만)
CREATE TABLE option_chain (
    symbol TEXT,
    asof_date DATE,
    expiry DATE,
    strike DOUBLE,
    option_type TEXT,            -- C / P
    last DOUBLE,
    bid DOUBLE,
    ask DOUBLE,
    volume INT,
    open_interest INT,
    iv DOUBLE,
    delta DOUBLE,                -- 계산값 (BSM)
    PRIMARY KEY (symbol, asof_date, expiry, strike, option_type)
);
```

## 디렉토리 확장

```
data/ingest/
├── ...
├── crypto_microstructure.py    # CCXT funding + OI + L/S
├── cboe_options.py             # CBOE VIX, SKEW, put/call CSV
├── deribit_options.py          # Deribit BTC/ETH 옵션 + DVOL
└── option_chain.py             # Yahoo (US) + KRX (KR) 종목별 옵션
```

## 비용 0원 도달

| 항목 | 무료 가능 |
|------|----------|
| Crypto funding/OI/L-S | ✓ CCXT 무한 |
| Crypto 청산 (실시간) | ✓ WS, 거래소 한도 내 |
| CBOE VIX/SKEW/Put-Call | ✓ CSV 직접 다운로드 |
| Yahoo 옵션 체인 | ✓ 비공식, 일일 한도 |
| Deribit BTC/ETH 옵션 | ✓ 무료 API |
| KOSPI200 옵션 | ✓ pykrx 일부, KRX 일부 |

## Point-in-time 주의

- **Funding rate**: 정산 시각 기준 — 정산 후에만 알 수 있는 값
- **Open Interest**: 거래소 측 집계 lag (1~5분) 인지
- **VIX/SKEW**: 장중 실시간 → 종가 기준만 사용 권장 (intraday 노이즈)
- **Put/Call ratio**: CBOE 일별 종가 기준
- **종목 옵션 체인**: yfinance는 거의 실시간이지만 비공식이라 종종 누락

## 안티패턴

- **Funding rate raw 값으로 거래** — 거래소별 다름 (Binance 0.01% vs Bybit 0.01%/8h 등). 표준화 필수
- **OI를 USD가 아닌 contract 수로 비교** — 거래소·심볼별 contract size 다름. notional value로
- **VIX 단일 값으로 거래** — VIX는 SP500의 IV 평균. **term structure (VIX/VIX3M)** 또는 **VIX 변화율**이 더 유용
- **Put/Call ratio raw 값** — 일중 변동 큼. 5일 또는 20일 이동평균
- **종목 옵션 IV 그대로 사용** — 어닝 임박 시 IV는 자연스럽게 상승. **IV percentile** (52주 분포 내 위치)이 정확
- **Deribit DVOL을 SPX 옵션과 동일시** — BTC/ETH는 변동성 자체가 5~10배. 정규화 후 비교

## 권장 추가 위치 — Stage 1.8

Stage 1.7 (Sentiment & Flow) 직후, Stage 2 백테스트 들어가기 전.
