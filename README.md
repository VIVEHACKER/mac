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
| [docs/CHART_READING.md](docs/CHART_READING.md) | **차트 리딩 엔진** — 11개 개념(FVG/OB/매물대/볼륨/호가/OI/와이코프/패턴) 탐지 알고리즘 명세 + 컨플루언스 프레임워크 |
| [docs/LIVE_OPERATIONS.md](docs/LIVE_OPERATIONS.md) | 실자금 자동운용 전환을 위한 live gate, halt, model promotion, dry-run 운영 런북 |
| [docs/DEPLOYMENT_READINESS.md](docs/DEPLOYMENT_READINESS.md) | **IDEAL 라인(aqr_top7_cap20_trail10) 배포 준비도** — 통계 유의성(PSR/DSR/부트스트랩), 재현성, 운영자 게이트 |
| [docs/COMPOUNDER_OPERATIONS.md](docs/COMPOUNDER_OPERATIONS.md) | **컴파운더(텐베거) 워치리스트 운영 런북** — 월간 절차, 도시에 읽는 법, 확신 체크리스트, 한계 |
| [docs/superpowers/](docs/superpowers/) | brainstorm→spec→plan 산출물 (통계엄밀성·컴파운더 P1/P2/P3 설계·구현 계획) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 9단계 구현 로드맵 + 검증 기준 |
| [setup/install.sh](setup/install.sh) | Mac 초기 셋업 자동화 |
| [CLAUDE.md](CLAUDE.md) | 다음 Claude Code 세션이 즉시 컨텍스트를 잡기 위한 핸드오프 |

## 빠른 시작 (Mac에서)

```bash
git clone <repo-url> trader && cd trader
bash setup/install.sh
uv run trader init
uv run trader backtest MSFT --start 2024-01-01 --end 2026-05-07 --lookback 63 --fee-bps 2 --output out/msft-backtest.md
```

## 지금 실행되는 MVP

루트의 `trader` 명령은 API 키 없이도 다음 경로를 실제로 실행한다.

```bash
# Yahoo 일봉 가격 수집 → DuckDB 저장
uv run trader ingest MSFT --start 2024-01-01 --end 2026-05-07

# 저장된 데이터 확인
uv run trader status
uv run trader bars MSFT --limit 5

# 다종목 모멘텀 스크리닝
uv run trader screen MSFT,AAPL,NVDA,AMZN,META,GOOGL,AVGO --start 2024-01-01 --end 2026-05-07 --lookback 126 --output out/momentum-screen.md

# 저장된 catalog로 time-series momentum 백테스트 (거래비용/최대노출 반영)
uv run trader backtest MSFT --start 2024-01-01 --end 2026-05-07 --lookback 63 --fee-bps 2 --max-position 1 --output out/msft-backtest.md

# 상위 N개 모멘텀 로테이션 포트폴리오 백테스트 (월간 리밸런싱 + 거래비용 + SPY 비교)
uv run trader portfolio MSFT,AAPL,NVDA,AMZN,META,GOOGL,AVGO --start 2024-01-01 --end 2026-05-07 --lookback 126 --top-n 3 --rebalance-days 21 --fee-bps 2 --benchmark SPY --benchmark-market us --output out/momentum-portfolio.md

# 2008-2020 SPY 비교 재현용: 2008년부터 상장 데이터가 있는 대형 기술주 유니버스
uv run trader portfolio AAPL,MSFT,NVDA,AMZN,GOOGL --start 2008-01-01 --end 2020-12-31 --lookback 126 --top-n 3 --rebalance-days 21 --fee-bps 2 --benchmark SPY --benchmark-market us --output out/us-momentum-spy-2008-2020.md

# 같은 유니버스의 train/test 파라미터 민감도 점검
uv run trader robustness AAPL,MSFT,NVDA,AMZN,GOOGL --start 2008-01-01 --end 2020-12-31 --split 2016-01-01 --lookbacks 63,126,252 --top-ns 1,2,3 --rebalance-days 21,63 --fee-bps 2 --benchmark SPY --benchmark-market us --output out/us-momentum-robustness-2008-2020.md

# 생존편향을 낮춘 investable ETF 유니버스 점검
# 기본 연구 유니버스는 46개 multi-asset ETF다. 주식 스타일/섹터, 해외주식, 국채 듀레이션, TIPS, IG/HY credit, 금/은/원자재/리츠, QID/SDS crash hedge를 포함한다.
uv run trader universe-audit ALL --start 2008-01-01 --end 2020-12-31 --universe-csv data/universes/multi-asset-etf-2008.csv --strict --output out/us-etf-multiasset-universe-audit-2008-2020.md
uv run trader portfolio ALL --start 2008-01-01 --end 2020-12-31 --lookback 126 --top-n 5 --rebalance-days 21 --fee-bps 2 --benchmark SPY --benchmark-market us --universe-csv data/universes/multi-asset-etf-2008.csv --output out/us-etf-multiasset-momentum-spy-2008-2020.md
uv run trader robustness ALL --start 2008-01-01 --end 2020-12-31 --split 2016-01-01 --lookbacks 63,126,252 --top-ns 3,5,8 --rebalance-days 21,63 --fee-bps 2 --benchmark SPY --benchmark-market us --universe-csv data/universes/multi-asset-etf-2008.csv --output out/us-etf-multiasset-momentum-robustness-2008-2020.md

# 작은 9개 ETF 유니버스는 빠른 sanity check용으로만 둔다.
uv run trader universe-audit ALL --start 2008-01-01 --end 2020-12-31 --universe-csv data/universes/liquid-etf-2008.csv --strict --output out/us-etf-small-universe-audit-2008-2020.md

# 수익률 개선 실험: 모멘텀 + 단기반전 페널티 + 저변동성 + SPY 추세필터 + 방어자산
uv run trader factor-portfolio ALL --start 2008-01-01 --end 2020-12-31 --momentum-lookback 126 --reversal-lookback 5 --volatility-lookback 21 --risk-filter-lookback 100 --top-n 8 --rebalance-days 21 --weighting inverse-vol --defensive-only --defensive-basket TLT,IEF,SHY,CASH --defensive-selection-lookback 63 --fee-bps 2 --benchmark SPY --benchmark-market us --universe-csv data/universes/multi-asset-etf-2008.csv --output out/us-etf-multiasset-top8-risk100-2008-2020.md

# 과최적화 방지: train에서 파라미터를 고르고 다음 test 창에서만 평가
uv run trader walk-forward ALL --start 2008-01-01 --end 2020-12-31 --train-years 5 --test-years 3 --step-years 1 --momentum-lookbacks 126 --reversal-lookbacks 5 --volatility-lookbacks 21 --top-ns 5,8 --risk-filter-lookbacks 0,100,200 --weighting-modes inverse-vol --rebalance-days-values 21 --defensive-symbols CASH --defensive-only --defensive-basket TLT,IEF,SHY,CASH --defensive-selection-lookback 63 --selection-metric return-drawdown --fee-bps 2 --benchmark SPY --benchmark-market us --universe-csv data/universes/multi-asset-etf-2008.csv --output out/us-etf-multiasset-top5-top8-walk-forward-2008-2020.md

# train 내부 마지막 2년을 검증창으로 떼어 선택 리스크를 더 보수적으로 점검
uv run trader walk-forward ALL --start 2008-01-01 --end 2020-12-31 --train-years 5 --validation-years 2 --test-years 3 --step-years 1 --momentum-lookbacks 126 --reversal-lookbacks 5 --volatility-lookbacks 21 --top-ns 5,8 --risk-filter-lookbacks 0,100,200 --weighting-modes inverse-vol --rebalance-days-values 21 --defensive-symbols CASH --defensive-only --defensive-basket TLT,IEF,SHY,CASH --defensive-selection-lookback 63 --selection-metric return-drawdown --fee-bps 2 --benchmark SPY --benchmark-market us --universe-csv data/universes/multi-asset-etf-2008.csv --output out/us-etf-multiasset-top5-top8-walk-forward-validated-2008-2020.md

# SPY 초과수익 기준선: 같은 PIT ETF 유니버스 안에서 QQQ 단일 노출을 명시 비교
uv run trader factor-portfolio QQQ --start 2008-01-01 --end 2020-12-31 --momentum-lookback 126 --reversal-lookback 5 --volatility-lookback 21 --risk-filter-lookback 0 --top-n 1 --rebalance-days 21 --weighting equal --fee-bps 2 --benchmark SPY --benchmark-market us --universe-csv data/universes/liquid-etf-2008.csv --no-fetch --output out/us-etf-qqq-baseline-2008-2020.md
uv run trader walk-forward QQQ --start 2008-01-01 --end 2020-12-31 --train-years 5 --test-years 3 --step-years 1 --momentum-lookbacks 126 --reversal-lookbacks 5 --volatility-lookbacks 21 --top-ns 1 --risk-filter-lookbacks 0 --weighting-modes equal --rebalance-days-values 21 --selection-metric annualized-excess --fee-bps 2 --benchmark SPY --benchmark-market us --universe-csv data/universes/liquid-etf-2008.csv --no-fetch --output out/us-etf-qqq-baseline-walk-forward-2008-2020.md

# 리스크 조정 개선형: QQQ는 위험자산, TLT는 방어자산으로만 사용
uv run trader factor-portfolio QQQ,TLT --start 2008-01-01 --end 2020-12-31 --momentum-lookback 63 --reversal-lookback 5 --volatility-lookback 21 --risk-filter-lookback 100 --top-n 1 --rebalance-days 21 --weighting equal --defensive-only --max-risk-weight 1.0 --fee-bps 2 --benchmark SPY --benchmark-market us --universe-csv data/universes/liquid-etf-2008.csv --no-fetch --output out/us-etf-qqq-tlt-defensive-only-2008-2020.md
uv run trader walk-forward QQQ,TLT --start 2008-01-01 --end 2020-12-31 --train-years 5 --test-years 3 --step-years 1 --momentum-lookbacks 63,126 --reversal-lookbacks 5 --volatility-lookbacks 21 --top-ns 1 --risk-filter-lookbacks 100,200 --weighting-modes equal --rebalance-days-values 21 --defensive-only --max-risk-weights 1.0 --drawdown-guards 0 --selection-metric return-drawdown --fee-bps 2 --benchmark SPY --benchmark-market us --universe-csv data/universes/liquid-etf-2008.csv --no-fetch --output out/us-etf-qqq-tlt-defensive-only-walk-forward-2008-2020.md

# 보수형: 위험자산 비중을 85%로 제한하고 초과분은 TLT/cash에 둠
uv run trader factor-portfolio QQQ,TLT --start 2008-01-01 --end 2020-12-31 --momentum-lookback 63 --reversal-lookback 5 --volatility-lookback 21 --risk-filter-lookback 100 --top-n 1 --rebalance-days 21 --weighting equal --defensive-only --max-risk-weight 0.85 --fee-bps 2 --benchmark SPY --benchmark-market us --universe-csv data/universes/liquid-etf-2008.csv --no-fetch --output out/us-etf-qqq-tlt-defensive-only-085-2008-2020.md
uv run trader walk-forward QQQ,TLT --start 2008-01-01 --end 2020-12-31 --train-years 5 --test-years 3 --step-years 1 --momentum-lookbacks 63,126 --reversal-lookbacks 5 --volatility-lookbacks 21 --top-ns 1 --risk-filter-lookbacks 100,200 --weighting-modes equal --rebalance-days-values 21 --defensive-only --max-risk-weights 0.85 --drawdown-guards 0 --selection-metric return-drawdown --fee-bps 2 --benchmark SPY --benchmark-market us --universe-csv data/universes/liquid-etf-2008.csv --no-fetch --output out/us-etf-qqq-tlt-defensive-only-085-walk-forward-2008-2020.md

# CCXT 공개 API로 크립토 일봉 수집/백테스트
uv run trader portfolio BTC/USDT,ETH/USDT,SOL/USDT --market crypto --start 2026-01-01 --end 2026-05-07 --lookback 30 --top-n 2 --rebalance-days 7 --fee-bps 5 --output out/crypto-momentum-portfolio.md

# pykrx로 한국 주식 일봉 수집/스크리닝/백테스트
uv run trader screen 005930,000660,005380,035420,051910 --market kospi --start 2024-01-01 --end 2026-05-07 --lookback 126 --output out/kr-momentum-screen.md
uv run trader portfolio 005930,000660,005380,035420,051910 --market kospi --start 2024-01-01 --end 2026-05-07 --lookback 126 --top-n 2 --rebalance-days 21 --fee-bps 5 --output out/kr-momentum-portfolio.md

# 펀더멘털/매크로/flow를 PIT 테이블에 저장
uv run trader fundamentals ALL --provider csv --file data/imports/us-fundamentals-pit.csv
uv run trader fundamentals 005930 --market kospi --provider manual --period-end 2025-12-31 --net-income 35000000000000 --free-cash-flow 28000000000000 --equity 300000000000000 --debt 5000000000000 --shares-out 5969782550 --eps 5863
uv run trader macro DGS10 --provider manual --date 2026-05-07 --value 4.12 --start 2026-05-07 --end 2026-05-07
uv run trader flows 005930 --market kospi --provider manual --date 2026-05-07 --investor foreign --net-value 125000000000
uv run trader flows 005930 --market kospi --provider csv --file data/imports/krx-flow-005930.csv

# AQR 팩터, valuation, entry ladder
uv run trader factor 005930,000660,005380,035420,051910 --market kospi --start 2024-01-01 --end 2026-05-07 --lookback 126 --output out/kr-aqr-factor.md
uv run trader valuate 005930 --market kospi --fair-value 320000
uv run trader entry 005930 --market kospi

# Pair trading 회귀 hedge-ratio/z-score 분석 + 비용/공매도 가능성 게이트
uv run trader pair MSFT AAPL --start 2024-01-01 --end 2026-05-07 --lookback 252 --entry-z 2 --validate --fee-bps 2 --slippage-bps 3 --min-sharpe 0 --max-drawdown 0.2 --shortability-csv data/imports/shortability.csv --require-shortability --max-borrow-fee-bps 500 --shortability-max-age-days 2 --min-shortability-confidence medium --output out/msft-aapl-pair.md

# 옵션 체인 CSV 또는 Yahoo 옵션 체인으로 VIX 방식 30일 변동성 계산
uv run trader vix-calc --file data/imports/spx-option-chain.csv --as-of 2026-05-07 --strict-quality --max-option-quote-age-days 7 --max-bid-ask-spread-pct 0.5 --store --output out/vix-calc.md
uv run trader vix-calc --source yahoo --underlying SPY --as-of 2026-05-07 --strict-quality --require-last-trade --max-option-quote-age-days 7 --max-bid-ask-spread-pct 0.5 --output out/spy-vix-calc.md

# 페이퍼 주문 시뮬레이션 + kill switch
uv run trader paper MSFT --side buy --qty 2 --price 420 --cash 10000
uv run trader risk-check --start-equity 10000 --current-equity 9950 --gross-exposure 0.8

# --- 통계 유의성 검증 (PSR/Deflated Sharpe/블록 부트스트랩) ---
# 전략 일별 수익률 시리즈 추출 → 유의성 배터리. engine/significance.py + scripts/significance_test.py
uv run trader factor-portfolio ALL --pit-universe SP100_PIT_2008 ... --returns-output out/variantN-returns.csv
# 결과·해석: out/significance-report.md, docs/DEPLOYMENT_READINESS.md (IDEAL 라인 배포 판정)

# --- 컴파운더(텐베거) 라인: 의사결정 지원 funnel (3 아키타입, 섹터 인지) ---
uv run python scripts/snapshot_fundamentals.py fundamentals-$(date +%F)          # 펀더멘털 핀(재현성)
uv run python scripts/fetch_index_constituents.py                                 # S&P400+600 구성종목(→universe CSV)
uv run python scripts/fetch_sectors.py --universe-csv data/universes/sp400-600-current.csv
uv run python scripts/fetch_latest_closes.py --universe-csv data/universes/sp400-600-current.csv
uv run trader compounder-scan ALL --universe-csv data/universes/sp400-600-current.csv \
  --snapshot data/snapshots/fundamentals-$(date +%F).csv \
  --sectors-csv data/sectors/sp400-600-current-sectors.csv --top-n 30 --no-fetch \
  --output out/compounder-scan.md
# 운영 절차·한계: docs/COMPOUNDER_OPERATIONS.md

# 실자금 전환 게이트: 정책/승격/영구 halt/dry-run 주문 의도
uv run trader live-policy
uv run trader live-price-ingest QQQ,TLT,QID,SDS --feed iex --catalog-db data/store/live-prices.duckdb
uv run trader live-readiness --require-order-submission --require-price QQQ:us,TLT:us,QID:us,SDS:us --max-price-age-days 2
uv run trader validate-model QQQ,TLT --start 2008-01-01 --end 2020-12-31 --benchmark SPY --benchmark-market us --universe-csv data/universes/liquid-etf-2008.csv --no-fetch --momentum-lookback 63 --ensemble-momentum-lookbacks 63,126 --reversal-lookback 5 --volatility-lookback 21 --risk-filter-lookback 100 --ensemble-risk-filter-lookbacks 100,200 --risk-filter-vote-threshold 0.5 --top-n 1 --rebalance-days 21 --weighting equal --defensive-only --defensive-basket TLT,CASH --defensive-selection-lookback 63 --volatility-target 0.18 --max-leverage 1.0 --train-years 5 --test-years 3 --step-years 1 --momentum-lookbacks 63,126 --reversal-lookbacks 5 --volatility-lookbacks 21 --top-ns 1 --risk-filter-lookbacks 100,200 --weighting-modes equal --rebalance-days-values 21 --selection-metric annualized-return --fee-stress-bps 2,5,10 --min-walk-forward-windows 4 --min-stress-windows 2 --min-stress-return 0.30 --record-gate --strategy-id qqq-tlt-live
uv run trader validate-model QQQ,TLT,QID,SDS --start 2008-01-01 --end 2020-12-31 --benchmark SPY --benchmark-market us --universe-csv data/universes/multi-asset-etf-2008.csv --no-fetch --momentum-lookback 63 --ensemble-momentum-lookbacks 63,126 --reversal-lookback 5 --volatility-lookback 21 --risk-filter-lookback 100 --ensemble-risk-filter-lookbacks 100,200 --top-n 1 --rebalance-days 21 --weighting equal --defensive-only --defensive-basket TLT,CASH --defensive-selection-lookback 63 --max-leverage 1.0 --crash-hedge-symbols QID,SDS --crash-hedge-weight 1.0 --crash-hedge-trigger-lookback 5 --crash-hedge-trigger-drawdown 0.05 --crash-hedge-hold-days 5 --crash-hedge-selection-lookback 42 --fee-bps 2 --train-years 5 --test-years 3 --step-years 1 --momentum-lookbacks 63 --reversal-lookbacks 5 --volatility-lookbacks 21 --top-ns 1 --risk-filter-lookbacks 100 --weighting-modes equal --rebalance-days-values 21 --selection-metric annualized-return --fee-stress-bps 2,5,10 --stress-windows gfc-crash:2008-09-01:2009-03-09,covid-crash:2020-02-15:2020-03-23 --min-stress-windows 2 --min-stress-return 0.30 --max-stress-drawdown 0.35 --record-gate --strategy-id qqq-tlt-qid-sds-crash
uv run trader model-gate --strategy-id qqq-tlt-defensive --params M63/R5/V21 --windows 8 --positive-test-rate 0.625 --avg-test-excess 0.0208 --worst-test-mdd 0.23 --fee-stress-passed --pit-audit-passed
uv run trader live-halt status
uv run trader live-drill status
uv run trader live-reconcile --broker fake --expected QQQ:us:2 --fake-position QQQ:us:2:200
uv run trader live-dry-run QQQ --side buy --qty 2 --price 100 --max-order-notional 1000
uv run trader live-submit QQQ --side buy --qty 2 --price 100 --order-type limit --limit-price 99.50

# Streamlit 대시보드
uv run streamlit run dashboard/app.py --server.port 8501
```

**대시보드 종목선정 탭**은 상단 "🎯 검증 선정" 패널에서 `scan_universe`(핀드 스냅샷)로 검증 유니버스 106종을 랭크해 전략이 실제 매수하는 top-N(★)을 진입/손절/목표와 함께 보여준다(`python -m scripts.scan_universe`와 동일·재현 가능). 하단 "커스텀 유니버스 랭킹"은 탐색용 라이브 랭커이며 US에서 입력을 비우면 검증 유니버스(106) 전체로 채운다(이전 8종 하드코딩 기본값 제거). 추천기 탭도 동일하게 빈칸이면 106 풀로 횡단면 점수를 계산한다.

`trader backtest`와 `trader portfolio`는 `--benchmark SPY --benchmark-market us`처럼 외부 벤치마크를 지정하면
전략 수익률, 벤치마크 수익률, 초과수익률을 같은 기간으로 비교한다.
`trader portfolio` 리포트는 벤치마크 Sharpe/MDD와 연도별 초과수익률을 함께 보여준다.
`trader robustness`는 lookback/top-N/rebalance 조합을 train/test로 나눠 과최적화 가능성을 점검한다.
`trader universe-audit`는 PIT 유니버스의 가격 커버리지, 종료 구성종목의 상폐수익, 선택적 PIT fundamentals 커버리지를 백테스트 전에 검사한다. `--strict`를 켜면 error가 있을 때 실패 코드로 막는다.
`trader portfolio`, `trader robustness`, `trader factor-portfolio`, `trader walk-forward`는 `--pit-universe`/`--universe-csv`가 있으면 같은 감사를 자동 preflight로 실행하고 error가 있으면 백테스트를 시작하지 않는다. `--skip-universe-audit`는 연구 중 임시 우회용이고, `--audit-require-fundamentals`/`--audit-no-require-delistings`로 엄격도를 조정한다.
`trader factor-portfolio`는 단순 모멘텀에 단기반전 페널티, 저변동성 점수, 선택적 value/quality 점수, inverse-vol/equal 비중, SPY 이동평균 리스크필터, TLT/cash 방어자산 전환을 붙여 검증한다. 실전 후보는 `--ensemble-momentum-lookbacks`, `--ensemble-risk-filter-lookbacks`, `--defensive-basket TLT,IEF,SHY,CASH`, `--volatility-target 0.18 --max-leverage 1.0`처럼 신호를 평균화하고, risk-off 자산을 바스켓에서 고르며, 과열 구간 총노출을 줄이는 옵션을 같이 검증한다. `--crash-hedge-symbols QID,SDS`는 지정 종목을 평상시 팩터 랭킹에서 제외하고, benchmark의 고점 대비 하락과 룩백 수익률 하락이 동시에 발생할 때만 별도 hedge sleeve로 편입한다.
`trader walk-forward`는 train 창에서 고른 조합을 다음 test 창에만 적용한다. `--risk-filter-lookbacks 0,100,200`, `--weighting-modes inverse-vol,equal`, `--rebalance-days-values 21,63`, `--defensive-symbols TLT,IEF,SHY,CASH`, `--max-risk-weights 0.7,0.85,1.0`, `--drawdown-guards 0,0.15`, `--selection-metric annualized-return|return-drawdown|risk-first`처럼 선택 후보와 선택 기준까지 검증 대상으로 둔다. `--min-stress-return 0.30`은 GFC/COVID/2022 같은 stress window마다 총수익 +30% 이상을 강제한다. `--validation-years N`을 주면 train 마지막 N년만 후보 선택 점수로 사용해 train 전체 과최적화 리스크를 추가로 점검한다.
`--defensive-only`는 TLT 같은 방어자산을 일반 랭킹 후보에서 제외하고 risk-off 전환이나 `--max-risk-weight` 초과분 배치에만 사용한다. 이 옵션이 QQQ/TLT 조합의 역할 혼선을 줄인다. 리스크필터와 드로다운가드는 정규 리밸런싱 날짜를 기다리지 않고 risk-off 신호 당일 방어자산/cash로 즉시 전환한다.
현재 2008-2020 ETF 결과는 두 층으로 본다. 46개 multi-asset ETF 유니버스는 후보군 부족 리스크를 줄이는 기본 연구판이다. `top8/inverse-vol/risk-filter100/defensive-basket TLT,IEF,SHY,CASH`는 전체기간 +192.38%, MDD 21.64%, 평균 후보 44개, 평균 보유 5.38개로 폭은 좋아졌지만 SPY 대비 연환산 초과수익은 -1.18%다. 같은 유니버스의 제한 walk-forward도 positive test rate 12.5%, 평균 test 연환산 초과수익 -7.21%이고, train 내부 2년 검증창을 둔 더 보수적인 walk-forward는 positive test rate 0.0%, 평균 test 연환산 초과수익 -8.28%라 라이브 크기 확대 근거로는 부족하다. 반대로 QQQ 단일 노출은 전체기간 +601.20%, walk-forward positive test rate 100.0%, 평균 test 연환산 초과수익 +10.62%를 냈지만, MDD가 49.40%라 집중 성장주 베타 리스크가 크다. QQQ/TLT 방어자산 분리형은 전체기간 +357.11%, MDD 23.04%, walk-forward positive test rate 62.5%, 평균 test 연환산 초과수익 +2.08%로 수익률은 낮아지지만 즉시 risk-off 전환까지 반영해 현재 수익/리스크 균형형으로 더 적합하다. QID/SDS crash hedge sleeve를 붙인 `QQQ/TLT/QID/SDS, trigger 5 bars/-5%, hold 5 days, hedge 100%` PIT 후보는 전체기간 +306.18%, 연환산 +11.39%, MDD 36.63%, GFC crash +64.47%, COVID crash +32.51%로 `--min-stress-return 0.30`은 통과한다. 그러나 `validate-model` 전체 승격 게이트는 positive test rate 25.0%, 평균 test 연환산 초과수익 -0.35%, worst test MDD 34.66%, fee stress 실패로 BLOCK이므로 실자금 자동운용 후보가 아니라 paper/shadow 추가 연구 후보로만 둔다.
`trader pair`는 두 종목의 로그 가격을 정렬한 뒤 OLS hedge ratio, spread z-score, 상관계수, 반감기를 계산해 pair-trading 진입/관망/청산 상태를 리포트한다. `--validate`를 켜면 rolling out-of-sample mean-reversion 검증을 실행하고 fee/slippage 차감 후 순수익, Sharpe, MDD, 거래 수 기준을 함께 표시한다. `--shortability-csv`/`--require-shortability`를 쓰면 숏 대상 종목이 실제 shortable인지, borrow fee 한도, 데이터 freshness, confidence 기준을 함께 게이트한다.
`trader vix-calc`는 `expiration,strike,call_bid,call_ask,put_bid,put_ask` 옵션 체인 CSV나 `--source yahoo --underlying SPY`의 yfinance 옵션 체인을 받아 CBOE VIX 방식의 variance-swap 근사로 30일 변동성을 계산한다. `--risk-free-rate`가 없으면 catalog의 `DGS10`을 자동 사용하고, 단일 만기·만기 미브래킷·부족한 strike·역전 bid/ask·오래된 last trade·과도한 bid/ask spread 같은 체인 품질 경고를 리포트한다. Yahoo 옵션 체인은 기본적으로 last trade 날짜가 있어야 하며, `--strict-quality`는 경고가 있으면 저장/출력을 실패 처리한다.
정적 종목 리스트는 리포트에서 생존편향 경고를 남긴다.
`--universe-csv`/`--pit-universe`를 쓰면 리밸런싱 날짜마다 point-in-time 구성종목만 후보로 사용한다.
팩터/워크포워드 백테스트는 지표 계산용 warmup 데이터를 `--start` 이전부터 읽지만, 성과 곡선과 벤치마크 비교는 지정한 `--start` 이후만 집계한다.
팩터의 value/quality는 각 리밸런싱 날짜 기준으로 `asof_ts <= rebalance_date`인 펀더멘털만 사용한다. 최신 펀더멘털 1개를 과거 전체 기간에 복사하지 않는다.
PIT fundamentals CSV는 `symbol,market,period_end,asof_ts,revenue,operating_income,net_income,free_cash_flow,total_assets,total_equity,total_debt,shares_out,eps,source` 컬럼을 받는다.
PIT 개별주 유니버스에서 상폐 종목을 들고 있는데 다음 가격이 없으면 기본적으로 실패한다. `--delisting-returns-csv`로 `symbol,market,ts,return_pct,source,confidence` CSV를 넣어야 해당 날짜의 상폐수익을 적용하고 현금화한다.
리포트는 `Delisting Returns Applied`와 `Ended Members Missing Delisting Return`을 표시한다. 종료된 구성종목에 상폐수익이 없으면 그 주식 유니버스 결과는 아직 불완전한 증거로 봐야 한다.
Yahoo 일봉은 배당 반영 조정가를 기준으로 수익률을 계산한다.
`trader backtest`는 저장된 가격 데이터가 없으면 Yahoo에서 자동 수집한 뒤
`data/store/trader.duckdb`에 저장하고, DuckDB catalog에서 다시 읽어 백테스트한다.
`--market crypto`는 기본적으로 CCXT/Binance 공개 OHLCV를 사용한다.
`--market kospi`와 `--market kosdaq`는 기본적으로 pykrx 공개 일봉을 사용한다.
매크로는 `FRED_API_KEY`가 있으면 FRED 공식 API를 먼저 사용하고, 없으면 FRED 기간 제한 CSV를 사용한다.
`DGS10`/`VIXCLS`는 FRED 장애 시 미 재무부 수익률곡선 XML과 CBOE VIX 공식 CSV를 먼저 쓰고, 그래도 실패할 때만 Yahoo fallback(`^TNX`, `^VIX`)으로 저장한다.
KRX flow는 pykrx 투자자별 매매 엔드포인트의 reported value만 main `flows` 테이블에 저장한다. KRX가 `LOGOUT`/빈 응답을 반환하면 자동 추정 fallback을 하지 않고 실패시킨다.
무인 KRX 세션이 막힌 환경에서는 KRX 정보데이터시스템에서 내려받은 CSV를 `--provider csv --file ...`로 넣으면 같은 reported/high 경로로 들어간다. `uv run trader quality --require-flow 005930 --flow-market kospi --strict`로 reported flow 누락을 배포 전에 막을 수 있다.
네이버 금융 투자자별 매매동향은 `uv run trader flows 005930 --market kospi --provider naver-estimate ...`처럼 명시적으로 요청했을 때만 사용한다. 이 값은 종가와 순매매량으로 추정한 값이라 `flow_estimates` quarantine 테이블에 저장되고, 기본 수급 신호에서는 제외된다.
`uv run trader quality --as-of YYYY-MM-DD --strict`는 누락·오래된 데이터와 비공식 출처를 실행 전에 걸러낸다. Quarantine된 추정 flow는 `info`로만 보고되어 strict gate를 막지 않는다.
펀더멘털·매크로·flow는 API 실패나 키 부재 상황에서도 `--provider manual`로 같은 PIT 테이블에 넣을 수 있다.
라이브 거래는 `LIVE_TRADING_ENABLED=true`와 `LIVE_TRADING_ACK_RISK=true`가 동시에 설정되지 않으면 코드에서 차단된다.
실자금 전환 경로는 `LIVE_STRATEGY_ID`, `LIVE_BROKER`, `LIVE_MAX_CAPITAL`, `LIVE_POLICY_VERSION`과 별도 최종 스위치 `LIVE_ORDER_SUBMISSION_ENABLED=true`까지 요구하고, 주문 의도는 `live-readiness`/`live-submit`/execution layer에서 model registry approval, stress-window evidence, broker-grade price source, live mark deviation check, paper/shadow drill streak, reconciliation, idempotency, pre-trade risk, daily order/notional cap, persistent halt latch를 통과해야 한다.
강한 백테스트라도 `validate-model` 또는 `model-gate` 승격 레지스트리에 통과 기록이 없거나, 최신 승인 기록에 최소 2개 stress window와 worst stress return +30% 이상 증거가 없으면 live 후보로 보지 않는다.
`live-price-ingest`는 Alpaca 최신 가격을 `alpaca:<feed>:latest_bar` 소스로 저장해 Yahoo/manual/test 가격이 실전 게이트를 우회하지 못하게 한다. `live-readiness`/`live-submit`/`live-price-ingest`는 기본적으로 `LIVE_CATALOG_DB` 또는 `data/store/live-prices.duckdb`를 써서 긴 백테스트가 연구 catalog를 잠가도 live gate가 분리된다. `alpaca-live`는 기본적으로 30일 paper, 10일 shadow drill을 요구하고 market order는 꺼져 있다.
`validate-model`은 walk-forward, fee stress, 파라미터 주변값, 지정 stress window를 한 번에 검증해 live 승격 전에 과최적화와 비용 민감도를 더 보수적으로 본다.
기존 `trading-copilot` 명령도 그대로 fallback된다.
Pair/VIX 기능은 Apache-2.0 `je-suis-tm/quant-trading`의 standalone 예제를 참고했지만, 현재 catalog/CLI/테스트 구조에 맞게 새로 구현했다.

## 차트 리딩 엔진 (Chart Reading)

`engine/chart/` 패키지는 stdlib(statistics, math)만으로 구현된 순수 함수 기반 차트 탐지 엔진이다.
OHLCV 시퀀스를 입력받아 11개 개념을 병렬 탐지하고, 가중 컨플루언스 점수(0–100)와 진입 판정을 반환한다.

| 개념 | 모듈 | 적용 시장 |
|------|------|----------|
| 시장구조 (Swing BOS/CHoCH/EQH-EQL) | `structure.py` | 전 시장 |
| FVG / IFVG | `fvg.py` | 전 시장 |
| 오더블록 / 브레이커 | `order_block.py` | 전 시장 |
| 유동성 풀·스윕·OTE·MSS | `liquidity.py` | 전 시장 |
| 매물대 (POC / Value Area / HVN-LVN) | `volume_profile.py` | 전 시장 |
| 볼륨 분석 (RVOL / OBV / CMF / 와이코프 볼륨) | `volume.py` | 전 시장 |
| 와이코프 매집/분산 (Phase A–E) | `wyckoff.py` | 전 시장 |
| 차트 패턴 (헤숄·삼각형·쐐기·플래그 등) | `patterns.py` | 전 시장 |
| 캔들 패턴 (단일·복합·삼선) | `candles.py` | 전 시장 |
| 호가 (L2 OBI / VAMP / 벽) | `orderbook.py` | **크립토 전용** (ccxt) |
| 미체결약정 / 펀딩 (OI 4사분면·스퀴즈·캐스케이드) | `open_interest.py` | **크립토 전용** (ccxt) |

컨플루언스 집계(`read.py`)는 활성화된 개념의 가중 득표를 합산해 `EntryState`를 결정한다:
`ENTER_NOW` / `SCALE_IN` / `WAIT_FOR_PULLBACK` / `AVOID`.

**CLI 사용법**

```bash
# 크립토: 호가(order book) + 미체결약정(open interest) 포함 4h 롱 리딩
uv run trader chart-read BTC/USDT --market crypto --tf 4h --direction long \
  --with-orderbook --with-oi --exchange binance

# 미국 주식: OHLCV 전용 탐지기 (호가/OI 없음)
uv run trader chart-read AAPL --market us --tf 1d --direction long

# 한국 주식
uv run trader chart-read 005930 --market kospi --tf 1d --direction long

# 데이터 소스 명시 (기본값: auto — catalog 우선, 없으면 live 수집)
uv run trader chart-read ETH/USDT --market crypto --tf 1h --direction short \
  --source live --with-orderbook --with-oi --exchange binance
```

리포트는 한국어로 출력되며 결정(진입/스케일인/대기/회피), 컨플루언스 점수, 개념별 가중 득표,
진입 가격대, 무효화(인밸리데이션) 레벨, 근거를 포함한다.

알고리즘 명세 전문: [docs/CHART_READING.md](docs/CHART_READING.md)

```bash
uv run trader quote MSFT --output out/quote-MSFT.md
uv run trader recommend MSFT --target-price 520 --stop-price 390 --output out/recommend-MSFT.md
```

## 비용

페이퍼 트레이딩 + 일봉 백테스트 + 크립토 라이브 = **0원**. 인트라데이 미국 데이터가 필요해질 때만 Alpaca SIP $99/월 또는 Polygon $79~$199/월 검토.
