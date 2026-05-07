# Sentiment & Flow Data

가격·펀더멘털·매크로와 다른 차원의 데이터:

- **자금 흐름 (Flow)**: 누가 사고 누가 파는지 — 기관/외국인/개인, 선물 포지션
- **감정 (Sentiment)**: 어떻게 인식되는지 — 뉴스 톤, 소셜 mention

시장 심리와 자금 동향을 추적해 momentum/contrarian 양쪽 알파에 활용.

## 데이터 소스

### 자금 흐름

| 소스 | 데이터 | 한계 | 라이브러리 |
|------|--------|------|-----------|
| **KRX 기관매매동향** | 시장/종목별 외국인·기관·연기금·개인 일별 매매 | 일별 (장 마감 18시 발표), 한국 한정 | `pykrx` (이미 사용) |
| **CFTC COT** (https://www.cftc.gov/MarketReports/CommitmentsofTraders) | 미국 선물 주간 포지션 (Commercial / Non-Commercial / Small) | 주 1회, 화요일 데이터 → 금요일 발표 | `cot_reports` |

### 감정 (Sentiment)

| 소스 | 데이터 | 한계 | 라이브러리 |
|------|--------|------|-----------|
| **GDELT** (https://www.gdeltproject.org) | 100+ 언어 글로벌 뉴스 + 톤 점수 + 인물/기업 추출 | 무료, BigQuery 1TB/월 또는 CSV 직접 | `gdeltdoc` 또는 `requests` |
| **Reddit (PRAW)** | WSB, r/stocks, r/investing, r/cryptocurrency mention/감정 | OAuth 키 필요 (무료), 60 req/min | `praw` |
| **StockTwits** (선택) | 종목별 bullish/bearish % | 비공식 API | `requests` |

### 한국 한정 (스크래핑 — 향후 검토)
- 네이버 종토방, 디시 주식갤러리 — TOS 주의, 일단 미포함

## 알파 매핑

| 데이터 | 알파 메커니즘 | 전략 모듈 |
|--------|-------------|-----------|
| KRX 외국인 순매수 N일 누적 | **외국인 매수 모멘텀** — 한국 시장 가장 강력 시그널 중 하나 | `signals/foreign_flow.py` |
| KRX 기관 순매수 N일 | **기관 매수 모멘텀** | `signals/institution_flow.py` |
| KRX 개인 순매수 + 외국인 순매도 | **개미 역지표** (전통적 contrarian) | `signals/retail_contrarian.py` |
| CFTC COT Commercial 극단 포지션 | **실수요자 헤지 → 가격 압력 선행** | `signals/cot_commercial.py` |
| CFTC Non-Commercial 95th percentile | **포지션 쏠림 → mean reversion** | `signals/cot_extreme.py` |
| GDELT 톤 점수 5일 변화 | **매크로 sentiment 모멘텀** | `signals/gdelt_tone.py` |
| GDELT 기업 mention 급증 | **이벤트 detection** (M&A, 사고 등) | `signals/gdelt_events.py` |
| Reddit WSB mention 급증 + bullish | **retail squeeze 사전 포착** (GME 류) | `signals/wsb_squeeze.py` |
| StockTwits 극단 sentiment | **mean reversion** 시그널 | `signals/stocktwits_extreme.py` |

## DuckDB 스키마

```sql
-- KRX 자금 흐름 (시장 + 종목)
CREATE TABLE krx_flows (
    date DATE,
    market TEXT,            -- KOSPI / KOSDAQ
    symbol TEXT,            -- NULL이면 시장 전체
    investor_type TEXT,     -- foreign / institutional / pension / retail / financial / corporate
    buy_value DOUBLE,
    sell_value DOUBLE,
    net_value DOUBLE,
    PRIMARY KEY (date, market, symbol, investor_type)
);

-- CFTC COT
CREATE TABLE cot_positions (
    report_date DATE,       -- 화요일 (데이터 시점)
    release_ts TIMESTAMP,   -- 금요일 15:30 ET (발표 시각, PIT)
    contract_code TEXT,
    contract_name TEXT,     -- GOLD, S&P 500 E-MINI, KRW, CRUDE OIL, etc
    category TEXT,          -- legacy / disaggregated / TFF
    commercial_long DOUBLE,
    commercial_short DOUBLE,
    noncomm_long DOUBLE,
    noncomm_short DOUBLE,
    nonreport_long DOUBLE,
    nonreport_short DOUBLE,
    open_interest DOUBLE,
    PRIMARY KEY (report_date, contract_code, category)
);

-- GDELT 뉴스 톤 (집계)
CREATE TABLE gdelt_tone (
    date DATE,
    country TEXT,           -- US / KR / EU / global
    theme TEXT,             -- ECON_INFLATION, ECON_BANKRUPTCY, FED_RATE, etc
    avg_tone DOUBLE,        -- -100 (negative) ~ +100 (positive)
    article_count INT,
    PRIMARY KEY (date, country, theme)
);

-- GDELT 기업/인물 mention
CREATE TABLE gdelt_mentions (
    date DATE,
    entity_name TEXT,       -- 기업명 또는 티커
    mention_count INT,
    avg_tone DOUBLE,
    top_sources TEXT,       -- JSON array of domain
    PRIMARY KEY (date, entity_name)
);

-- Reddit 종목 언급
CREATE TABLE reddit_mentions (
    date DATE,
    symbol TEXT,
    subreddit TEXT,         -- wallstreetbets / stocks / investing / cryptocurrency
    mention_count INT,
    bullish_count INT,      -- 본문/제목에서 추출
    bearish_count INT,
    avg_upvotes DOUBLE,
    avg_comments DOUBLE,
    PRIMARY KEY (date, symbol, subreddit)
);
```

## Point-in-time 주의사항

| 소스 | 발표 시각 | PIT 규칙 |
|------|---------|---------|
| KRX 매매동향 | 장 마감 후 18:00 KST | 다음 거래일 시가부터 사용 |
| CFTC COT | 금요일 15:30 ET (화요일 마감 데이터) | release_ts 이후만, 3일 lag 인지 |
| GDELT | 15분마다 업데이트 | 거의 실시간 가능 |
| Reddit | 실시간 | 게시 시각 + 30~60분 lag (확산 시간) 권장 |

## 권장 추가 순서

1. **KRX 기관매매** (Stage 1.7-a) — pykrx에 이미 함수 있어 수집만
2. **CFTC COT** (Stage 1.7-b) — 데이터양 적음 (주 1회), 매크로 강화
3. **GDELT 톤** (Stage 1.7-c) — 글로벌 sentiment, BigQuery 무료
4. **Reddit WSB mention** (Stage 1.7-d) — PRAW 키 신청 후 종목 mention
5. **StockTwits** (선택) — 빠른 추가 가능

## 디렉토리 확장

```
data/
├── ingest/
│   ├── ...
│   ├── krx_flows.py             # 신규: KRX 투자자별 매매
│   ├── cot_cftc.py              # 신규: CFTC COT 주간 보고
│   ├── gdelt_news.py            # 신규: GDELT 톤 + 기업 mention
│   └── reddit_mentions.py       # 신규: Reddit 종목 언급
└── store/
    └── sentiment/               # 신규: sentiment + flow 데이터
```

## 비용 0원 도달

| 항목 | 무료 가능 |
|------|----------|
| KRX 매매동향 | ✓ pykrx 무한 |
| CFTC COT | ✓ 정부 데이터, 무한 |
| GDELT 뉴스 + 톤 | ✓ BigQuery 1TB/월 무료 + CSV 직접 |
| Reddit | ✓ PRAW 60 req/min, OAuth 키 발급 |
| StockTwits | ✓ 비공식 API, 일일 한도 있음 |
| 자체 LLM sentiment | △ Anthropic API ~$1-5/일 (선택) |

## 안티패턴

- **KRX 일별 매매를 장중 거래에 사용** — 발표 시각(18:00) 이후만
- **COT release_ts 무시** — 화요일 데이터를 화요일 거래에 쓰면 look-ahead. 금요일 이후만
- **Reddit mention raw count만으로 판단** — bot/spam 필터링 필수 (계정 나이, karma, 도배 패턴)
- **GDELT 한국어 톤 직접 신뢰** — 한국어 모델 정확도 낮음. 한국 뉴스는 자체 LLM 분석 권장
- **단일 sentiment 시그널 거래** — sentiment는 노이즈 많음. 다른 알파와 결합 필수
- **WSB squeeze 추격매수** — 이미 폭등 후엔 진입 위험. 초기 mention surge에서만
- **외국인 단일일 매매로 판단** — 5일/20일 누적 모멘텀이 더 안정적
