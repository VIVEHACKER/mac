# 데이터 소스 — 한계와 우회

## 결정: Alpaca + pykrx + CCXT (모두 무료)

## 1. Alpaca (미국 주식/ETF)

### 가능
- 페이퍼 트레이딩 무료 (실시간 동작 검증)
- 라이브 거래 무료 (commission 0)
- IEX 데이터 무료 (일봉 신뢰 가능)
- 100+ ETF 인스턴트 사용 가능

### 한계
- **무료 데이터는 IEX 풀만** — 전체 미국 거래량의 ~2.5%
  - 일봉 OHLCV는 신뢰 가능 (가격 형성은 충분)
  - NBBO/스프레드는 부정확 → 마이크로 전략 불가
- 분봉/틱 데이터는 IEX 한정 → 인트라데이 백테스트 어려움

### 우회
- **일봉 전략부터 시작** — IEX 한계 영향 거의 없음
- 인트라데이 필요해지면 SIP 데이터 $99/월 (Algo Trader Plus)
- 또는 Polygon.io $79~$199/월

### API 키
- https://app.alpaca.markets → Paper 계정 생성 → API Key/Secret
- `.env`: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL=https://paper-api.alpaca.markets`

---

## 2. pykrx (한국 주식)

### 가능
- KOSPI/KOSDAQ 일봉 OHLCV (수십 년치)
- 시가총액, PER, PBR, 배당
- 외국인/기관 보유율, 매매 동향
- 지수 데이터 (KOSPI200 등)

### 한계
- **인트라데이 데이터 없음** — 일봉만
- **거래 실행 불가** — 데이터 조회 전용
- KRX 사이트 크롤링 기반 → 가끔 구조 변경에 취약
- 실시간 호가/체결가 없음

### 우회 (라이브 필요해질 때)
- **KIS OpenAPI (한국투자증권)** — 무료, 신청 필요
  - 모의투자 + 실거래 모두 지원
  - REST + WebSocket
  - 인트라데이 데이터 + 호가 + 주문
  - Python: `mojito` 또는 `pykis` 라이브러리
- 키움 OpenAPI는 Windows COM이라 Mac 비추천

### 시작 전략
- pykrx로 일봉 백테스트 (Stage 2)
- 라이브 운용 결정 시 KIS API 추가 (Stage 5)

---

## 3. CCXT (크립토)

### 가능
- 100+ 거래소 통합 인터페이스
- 일봉/분봉/틱/실시간 WebSocket 모두 무료
- 라이브 거래 (각 거래소 API 키)
- 24/7 운영, 변동성 풍부 → ML 실험 최적

### 한계
- 거래소별 rate limit (Binance: 1200 req/min)
- 과거 분봉은 페이지네이션 필요 (1000개씩)
- 거래소마다 심볼/페이/레버리지 차이 → 정규화 필요

### 권장 거래소
- **Binance** — 유동성 1위, 코인/페이 풍부, 한국 IP 제한 우회 필요
- **Bybit** — 한국 IP 가능, 선물 강점
- **Upbit** — 한국 KRW 페어, 하지만 종목 제한적

### API 키
- 거래소별 → API Management → 권한: Read + Spot Trade (Future은 별도)
- IP whitelist 강력 권장
- `.env`: `BINANCE_API_KEY`, `BINANCE_SECRET`, ...

---

## 데이터 저장 구조

```
data/store/
├── eod/                        # 일봉 (Parquet, 날짜 파티션)
│   ├── market=us/year=2024/...
│   ├── market=kr/year=2024/...
│   └── market=crypto/year=2024/...
├── intraday/                   # 분봉 (크립토 중심)
│   └── market=crypto/symbol=BTCUSDT/...
└── catalog.duckdb              # 메타테이블

테이블 스키마 (DuckDB):
  symbols(symbol, market, name, listed_at, delisted_at, sector)
  bars_eod(symbol, date, open, high, low, close, volume, vwap, source)
  bars_min(symbol, ts, open, high, low, close, volume, source)
  fundamentals(symbol, asof, per, pbr, mcap, shares_out, ...)
  positions(strategy, symbol, qty, avg_cost, asof)
  trades(strategy, symbol, side, qty, price, ts, broker)
```

## 수집 주기

| 소스 | 주기 | 트리거 |
|------|------|--------|
| Alpaca EOD | 매일 1회 (장 마감 후 30분) | cron |
| pykrx EOD | 매일 1회 (한국 장 마감 후) | cron |
| CCXT 1m | 5분마다 (전략별 필요시) | scheduler |
| CCXT realtime | 항상 | WebSocket |
