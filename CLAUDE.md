# 다음 Claude Code 세션 핸드오프

## 즉시 알아야 할 것

이 프로젝트는 **Mac Mini M4 Pro 64GB**에서 실행 예정. Windows에서 설계만 했고 코드는 아직 0줄.

**목표**: 전세계 최고 수익률 펀드(Renaissance, Bridgewater, AQR, Citadel 등)의 투자 방식을
M4 Pro에서 실행 가능한 통합 트레이딩 시스템으로 구현.

**스택**: Nautilus Trader + Polars + DuckDB + Streamlit + PyTorch(MPS)
**시장 데이터**: Alpaca + pykrx + CCXT (모두 무료)
**미시경제 데이터**: SEC EDGAR + DART OpenAPI + FMP + yfinance (펀더멘털/공시/인사이더)
**거시경제 데이터**: FRED + ALFRED vintage + 한국은행 ECOS (regime/yield curve/CPI)
**Sentiment & Flow**: KRX 투자자별 매매 + CFTC COT + GDELT + Reddit
**파이프라인**: Backtest → Paper → Live (동일 코드)

## 첫 액션 순서

1. `bash setup/install.sh` 실행
2. `.env`에 키 입력:
   - Alpaca paper, Binance testnet (시장 데이터)
   - DART API key (https://opendart.fss.or.kr 즉시 발급)
   - FMP API key (https://site.financialmodelingprep.com 무료 250req/day)
   - FRED API key (https://fred.stlouisfed.org 즉시) — 거시 미국
   - ECOS API key (https://ecos.bok.or.kr/api/ 즉시) — 거시 한국
   - Reddit OAuth (https://www.reddit.com/prefs/apps → script 타입) — sentiment
   - SEC EDGAR / GDELT / CFTC COT는 키 불필요
3. **Stage 1 시작** — `data/ingest/alpaca_us.py` 부터 TDD로
4. **Stage 1.5** — 펀더멘털/공시 수집 + point-in-time 카탈로그
5. **Stage 1.6** — FRED + ECOS 거시 데이터 + vintage(ALFRED) + regime classifier
6. **Stage 1.7** — KRX flows + CFTC COT + GDELT + Reddit (sentiment & flow)

## 문서 우선순위

읽는 순서대로:
1. `README.md` — 전체 개요
2. `docs/STRATEGIES.md` — 어떤 펀드의 어떤 전략을 구현할지
3. `docs/ARCHITECTURE.md` — 시스템 구조 + 디렉토리 트리
4. `docs/DATA_SOURCES.md` — 시장 데이터 소스 한계와 우회법
5. `docs/MICROECONOMIC_DATA.md` — 펀더멘털/공시/인사이더/대안 데이터 + point-in-time
6. `docs/MACROECONOMIC_DATA.md` — FRED/ECOS 거시 + vintage + regime 4사분면
7. `docs/SENTIMENT_FLOW.md` — KRX 투자자별 매매 + CFTC COT + GDELT + Reddit
8. `docs/ROADMAP.md` — 8단계 로드맵 + 검증 기준 (Stage 1.5, 1.6, 1.7 포함)

## 작업 규칙 (이전 세션에서 합의)

- **Stage별 검증 기준 통과 후에만 다음으로** — "아마 될거야" 금지
- **TDD 우선** — strategies/는 fixture 기반 테스트 필수
- **단일 Strategy 인터페이스** — backtest/paper/live 코드 동일하게 유지
- **kill_switch 항상 켬** — 라이브 전환 시 일일 DD/포지션 한도 강제
- **시작은 일봉 전략** — IEX 데이터 한계와 무관하게 동작하는 영역부터
- **Point-in-time 강제** — 펀더멘털/공시는 발표일(asof) 이후만 사용. catalog.py가 `as_of=` 파라미터 강제
- **매크로 vintage 사용** — FRED ALFRED로 발표 시점 값 조회. 현재 revision 값으로 과거 백테스트 금지
- **데이터 소스 교차검증** — yfinance ↔ SEC EDGAR ±5% 이상 차이 시 EDGAR 신뢰

## 가장 먼저 구현할 전략

**AQR 팩터 (Value + Momentum + Quality)** — 학술적으로 가장 잘 검증됨, 일봉만으로 충분, 3시장 모두 적용 가능.

12-1 month 모멘텀부터 시작 → 미국 시장에서 Sharpe 0.6~1.0 재현 → 한국/크립토로 확장.

## 결정된 사항 (재논의 불필요)

| 항목 | 결정 | 이유 |
|------|------|------|
| 백테스트 엔진 | Nautilus Trader | Rust 코어 + 페이퍼/라이브 동일 코드 |
| DataFrame | Polars | pandas보다 5~30배 빠름, M4 최적 |
| 시계열 저장 | DuckDB + Parquet | 64GB RAM에 거의 다 캐시 |
| 첫 데이터 소스 | Alpaca + pykrx + CCXT | 모두 무료. Polygon/SIP는 알파 검증 후 |
| 첫 전략 | AQR 12-1 모멘텀 | 학술 검증 + 데이터 한계 무관 |
| ML | PyTorch MPS + XGBoost | M4 GPU 활용 |
| UI | Streamlit | 가장 빠른 개발 |
| 라이브 시작 자본 | 입금액의 5% | 알파 검증 단계적 증액 |

## 분석한 펀드 6개 (참고용)

1. **Renaissance Medallion** — 39% 순수 퀀트, ML
2. **Bridgewater Pure Alpha** — Risk Parity, 매크로
3. **AQR** — 팩터 (가치/모멘텀/퀄리티)
4. **Citadel** — Pod 멀티전략 ($397B)
5. **Pershing Square** — 액티비스트 집중투자
6. **Jane Street** — 마켓메이킹 + ETF 차익

상세는 `docs/STRATEGIES.md` 참조. 우리 시스템에는 1~4번이 직접 매핑, 5는 시그널만, 6은 크립토 한정.

## 비용 0원 도달 가능 범위

- 페이퍼 트레이딩: 무한
- 일봉 백테스트: 무한
- 크립토 라이브: 가능
- 미국/한국 라이브: 알파 검증 후 결정

## 환경 변수 검증

작업 시작 전 확인:
```bash
uv run python -c "
import os
from dotenv import load_dotenv
load_dotenv()
assert os.getenv('ALPACA_API_KEY'), 'Alpaca key missing'
assert os.getenv('BINANCE_API_KEY'), 'Binance key missing'
print('✓ env loaded')
"
```
