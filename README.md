# trader-design

전세계 최고 수익률 펀드(Renaissance, Bridgewater, AQR, Citadel, Pershing Square, Jane Street)의
투자 방식을 분석하고, Mac Mini M4 Pro에서 실행 가능한 통합 트레이딩 시스템으로 구현하는 프로젝트의 설계 문서.

## 환경

- **Target machine**: Mac Mini M4 Pro 64GB / 2TB SSD
- **Stack**: Nautilus Trader (Rust core + Python API) + Polars + DuckDB + Streamlit
- **Markets**: 미국 주식/ETF (Alpaca) + 한국 주식 (pykrx) + 크립토 (CCXT)
- **펀더멘털·공시**: SEC EDGAR (미국) + DART OpenAPI (한국) + FMP (어닝)
- **거시경제**: FRED + ALFRED vintage (미국) + 한국은행 ECOS API (한국)
- **자금 흐름·감정**: KRX 투자자별 매매 + CFTC COT + GDELT 뉴스 + Reddit
- **파생·마이크로구조**: Crypto funding/OI (CCXT) + CBOE VIX·SKEW·Put-Call + Deribit BTC/ETH 옵션
- **Valuation·진입**: DCF + Peer Multiples + RIM 통합 fair value, MoS + ATR ladder 진입가 추천
- **Pipeline**: Backtest → Paper → Live (동일 코드, broker만 교체)

## 문서 맵

| 문서 | 내용 |
|------|------|
| [docs/STRATEGIES.md](docs/STRATEGIES.md) | 분석한 6개 펀드 전략 + 우리 시스템에 어떻게 매핑되는지 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 시스템 구조, 디렉토리 트리, 데이터 흐름 |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | 시장 데이터 소스 (Alpaca/pykrx/CCXT) 한계와 우회법 |
| [docs/MICROECONOMIC_DATA.md](docs/MICROECONOMIC_DATA.md) | 미시경제 데이터 (펀더멘털/공시/인사이더/대안) |
| [docs/MACROECONOMIC_DATA.md](docs/MACROECONOMIC_DATA.md) | 거시경제 데이터 (FRED/ECOS, regime, vintage PIT) |
| [docs/SENTIMENT_FLOW.md](docs/SENTIMENT_FLOW.md) | 자금 흐름 + 감정 (KRX 기관매매 / CFTC COT / GDELT / Reddit) |
| [docs/DERIVATIVES_DATA.md](docs/DERIVATIVES_DATA.md) | 파생/마이크로구조 (Crypto funding+OI / CBOE VIX·SKEW·Put-Call / Deribit / 옵션 체인) |
| [docs/VALUATION.md](docs/VALUATION.md) | **적정가·고저평가·진입가** (DCF / Multiples / RIM / 크립토 NVT-MVRV / MoS ladder) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 9단계 구현 로드맵 + 검증 기준 |
| [setup/install.sh](setup/install.sh) | Mac 초기 셋업 자동화 |
| [CLAUDE.md](CLAUDE.md) | 다음 Claude Code 세션이 즉시 컨텍스트를 잡기 위한 핸드오프 |

## 빠른 시작 (Mac에서)

```bash
git clone <repo-url> trader && cd trader
bash setup/install.sh
# 이후 Claude Code 켜면 CLAUDE.md를 자동 로드
```

## 비용

페이퍼 트레이딩 + 일봉 백테스트 + 크립토 라이브 = **0원**. 인트라데이 미국 데이터가 필요해질 때만 Alpaca SIP $99/월 또는 Polygon $79~$199/월 검토.
