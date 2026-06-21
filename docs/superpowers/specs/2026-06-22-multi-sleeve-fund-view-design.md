# 멀티 슬리브 펀드 통합 뷰 — 설계 (Design Spec)

- 날짜: 2026-06-22
- 상태: 승인됨 (구현 대기)
- 범위: `trader-fund` 대시보드에 조립된 바벨 펀드 북을 보여주는 새 탭 1개 추가
- 리포: `trader-fund` (자체 git repo)

## 1. 목표

사용자의 펀드를 "8종목 모멘텀 한 슬리브"가 아니라 **전체 바벨(코어·헌트·모멘텀·리저브)이 한 화면에** 보이게 한다. 이미 존재하는 펀드 조립기(`engine/fund_book.assemble_fund_book`)의 결과를 Streamlit 탭으로 노출한다. **새 펀드 로직은 만들지 않는다** — 기존 조립 결과의 뷰만 만든다.

1차 목적(사용자 확정): **목표 배분 + OOS 성과를 한 탭에**.

## 2. 현황 (검증된 사실)

- `trader`와 `trader-fund`는 같은 코드 계보. `trader`=단일전략 연구엔진, `trader-fund`=펀드 조립 레이어(권위본).
- `engine/fund_book.py`:
  - `SleeveTarget(name, fraction, weights: dict[str,float])`
  - `assemble_fund_book(sleeves, *, max_name_weight=0.08) -> FundBook`
  - `FundBook(positions, sleeve_fractions, invested, reserve_cash, max_name_weight, top_name_weight, n_positions)` — `positions[i]`: symbol·fund_weight·sleeve provenance·capped
  - `format_fund_book(book) -> str`
- 4 슬리브: 코어(`engine/core_basket.select_core_basket`, 35%) · 헌트(`engine/hunt_basket.select_hunt_basket`, 15%) · 모멘텀/IDEAL(`engine/momentum_basket.select_momentum_basket`, 25% opt-in, 유일 검증 엣지) · 브릿지(`engine/countercyclical_bridge`, dry-powder). 조립기=조립 로직 자체.
- `engine/fund_exposure.py`: `compute_exposure(book, sectors)` / `format_exposure` — 섹터·슬리브기여·집중도(유효종목수, top).
- `engine/fund_book_oos.py`: 포워드 원장 채점 — `FundBookOOSEntry` / `FundBookOOSRecord(n_periods, cumulative_excess, annualized_excess, hit_rate, excess_sharpe, ...)`. 원장 파일 `out/fund-book-oos.jsonl`.
- `scripts/fund_book.py`: CLI. **조립 로직이 `main()` 안에 인라인** (재사용 함수 없음). 기본값: 유니버스 `data/universes/sp400-600-current.csv`, 섹터 `data/sectors/sp400-600-current-sectors.csv`, fundamentals `data/snapshots/fundamentals-2026-06-01-gp2.csv`, prices `data/snapshots/prices-2026-06-01.csv`, DB `../trader/data/store/trader.duckdb`. 모멘텀은 `--price-history` + `--momentum-snapshot` opt-in.
- 현재 대시보드 `trader-fund/dashboard/app.py`: 3탭(Catalog / Momentum / Valuation). 조립된 FundBook은 **안 보여줌** = 이 갭이 본 작업.

### 2.1 검증된 실행 결과 (헤드리스, trader 스냅샷 사용)
```
sleeves: core=35% hunt=15% momentum=25% | invested=60.0% reserve=40.0% cap=8% n=20
CL 5.00% momentum→ | AAL 2.69% core→ | MU 2.13% momentum→ | INTC 2.10% momentum→ ...
섹터/슬리브기여/유효종목수 18.5 — 익스포저 동작
```
(이 런은 hunt 바스켓이 비어 invested=60%; 인사이더 시그널 부재 시 그 15%는 리저브. 정상 동작.)

### 2.2 데이터 플러밍 핵심 사실
- **trader-fund의 스냅샷 CSV는 gitignore됨** — 매니페스트만 추적. 실물 CSV는 `../trader/data/snapshots/`에 존재(확인됨: fundamentals-2026-06-01-gp2.csv, prices-2026-06-01.csv, prices-ideal-2026-06-01.csv, fundamentals-2026-06-01-gp.csv).
- 유니버스/섹터 CSV는 trader-fund에 추적됨.
- `out/fund-book-oos.jsonl`은 **아직 미생성** → 성과 패널은 표본 0 상태부터 시작.

## 3. 접근법 결정

**채택: 추출 + 재사용, trader-fund 대시보드 새 탭.**
- 기각1(조립 로직 복붙): 슬리브 배선 중복 → 드리프트. `scan_universe` 통합에서 피한 안티패턴.
- 기각2(CLI stdout 파싱): 포맷 텍스트 파싱 취약, 구조화 표 불가.

## 4. 아키텍처

### 4.1 추출 (behavior-preserving refactor)
`scripts/fund_book.py`의 `main()` 조립 로직을 순수 함수로 추출:

```python
def build_fund_book(
    *,
    as_of: date | None = None,
    snapshot: Path = DEFAULT_SNAPSHOT,
    prices: Path = DEFAULT_PRICES,
    universe_csv: Path = DEFAULT_UNIVERSE,
    sectors_csv: Path = DEFAULT_SECTORS,
    db: Path = DEFAULT_DB,
    core_fraction: float = 0.35,
    hunt_fraction: float = 0.15,
    momentum_fraction: float = 0.25,
    max_name_weight: float = 0.08,
    price_history: Path | None = None,       # 모멘텀 시계열 (opt-in)
    momentum_snapshot: Path | None = None,   # 모멘텀 megacap fundamentals (재현)
    momentum_universe: Path | None = None,
    momentum_top_n: int = 7,
    momentum_cap: float = 0.20,
) -> tuple[FundBook, dict]:   # (book, sectors)
    ...
```
- `main()`은 argparse 후 이 함수를 호출하도록 변경. **출력·동작 불변** (회귀 테스트로 보장).
- 반환에 `sectors`를 포함해 호출측이 `compute_exposure(book, sectors)` 가능.

### 4.2 데이터 경로 리졸버
스냅샷 CSV가 trader-fund 로컬에 없을 때 trader로 폴백:

```python
def _resolve_snapshot(name: str) -> Path | None:
    local = ROOT / "data" / "snapshots" / name
    if local.exists():
        return local
    sibling = ROOT.parent / "trader" / "data" / "snapshots" / name
    return sibling if sibling.exists() else None
```
- 모멘텀 **기본 ON**: `prices-ideal-2026-06-01.csv`(시계열) + `fundamentals-2026-06-01-gp.csv`(megacap)를 리졸버로 찾으면 켜고, 못 찾으면 코어+헌트만(graceful).
- 어떤 스냅샷도 못 찾으면 탭에 정직한 안내(재생성 명령) 출력 후 중단.

### 4.3 위치
- `trader-fund/dashboard/app.py`: 탭 리스트 3 → 4 (`💹 펀드 포트폴리오` 추가) + 디스패치 추가.
- 신규 `trader-fund/dashboard/fund_portfolio.py`: `render_fund_portfolio()` + `@st.cache_data` 조립 헬퍼.

## 5. 컴포넌트 / 데이터 흐름

```
[as-of, 모멘텀토글, 경로]  →  _resolve_snapshot(...)  →  build_fund_book(...)
        →  (FundBook, sectors)  →  compute_exposure(book, sectors)
        →  fund_book_oos: load out/fund-book-oos.jsonl → score → FundBookOOSRecord
        →  render: 막대 / 표 / 섹터 / 성과
```
- `@st.cache_data`로 `(book_rows, meta, exposure_rows, oos)` 캐시(키 = as_of + 경로 + 모멘텀토글). 반환은 plain dict/list(피클 안전).

## 6. UI 레이아웃

```
💹 펀드 포트폴리오
[as-of ▼]  [☑ 모멘텀 슬리브 포함]  [↻ 새로고침]

상단 — 슬리브 배분
  정책 타겟:   ▮▮▮▮ core 35%  ▮▮ hunt 15%  ▮▮▮ momentum 25%  ░░ reserve 25%
  실현(조립후): invested 60% · reserve 40% · 종목캡 8% · n=20 · 유효종목수 18.5 · top CL 5.0%
  ※ 정책 타겟과 실현이 다를 수 있음(빈 슬리브·캡 초과 → 리저브로). 위 숫자는 검증 런 기준 예시.

중단 — 종목 / 섹터
  | 종목 | 펀드% | 캡 | 출처슬리브 |
  | CL   | 5.00 |  - | momentum  |
  | AAL  | 2.69 |  - | core      |  ...
  섹터: Unknown 25% · financials 10.8% · consumer 8.1% ...

하단 — OOS 성과 (fund_book_oos)
  누적초과 +x% · 연환산초과 +x% · 적중률 x% · excess Sharpe x.x · n_periods=k
  ※ 원장 미가동(표본 0)이면: "포워드 원장 미시작 — scripts/fund_book_oos.py로 PIT 기록 시작" 안내
```

정직성 캡션(상시 노출): "알파 주장 없음 · core/hunt 검증 엣지 없음 · momentum만 검증(+8.15%/yr walk-forward, US 한정) · 리스크 모델 아님(서술적 진단)".

## 7. 에러 / 빈값 처리
- 스냅샷 전부 없음 → `st.warning` + 재생성 명령, return.
- hunt 빈 바스켓 → 종목 적게 표시(정상, invested↓ reserve↑). 경고 아님.
- 모멘텀 토글 OFF 또는 시계열 없음 → core+hunt만, 배너로 "모멘텀 제외" 표기.
- OOS 원장 미생성/표본<요건 → 성과 수치 대신 "표본 부족" 정직 표기.
- 조립 예외(ValueError/FileNotFoundError) → `st.error`로 메시지 표면화(탭만 실패, 앱은 유지).

## 8. 테스트
- `tests/test_scripts/test_fund_book.py`(신규 또는 확장): 추출 `build_fund_book(...)`가 기존 `main()`과 **동일 결과**(positions·fractions·invested) 생성 — CLI 패리티.
- `tests/test_dashboard/test_fund_portfolio.py`(신규): 캐시 헬퍼가 trader 스냅샷 폴백으로 FundBook rows·meta·exposure를 정상 반환(헤드리스, `scan_universe` 검증과 동형). OOS 빈 원장 경로도 커버.
- Streamlit 렌더 자체는 단위테스트 안 함(헤드리스 데이터 함수까지만).
- 회귀: `gan-harness verify` 또는 타깃 pytest 서브셋(풀 스위트 직접 실행 금지 — exit 144).

## 9. 범위 밖 (YAGNI)
- 두 대시보드 병합 / trader 8탭으로 이전.
- 실제 $ NAV·P&L 라이브 북(바벨엔 $ 북 없음 — 별도 작업).
- 정책 비중(35/15/25) 실시간 슬라이더 풀 편집(MVP는 기본값 표시; as-of·모멘텀토글만).
- 브릿지 dry-powder 시각화(리저브에 포함만, 별도 패널 없음).

## 10. 리스크 / 메모
- 추출 refactor가 `scripts/fund_book.py`(검증 경로 인접)를 건드림 → CLI 패리티 테스트로 회귀 차단 필수.
- 스냅샷이 gitignore라 다른 머신/CI에선 trader 폴백도 없을 수 있음 → "재생성" 안내가 graceful degrade 경로.
- 두 Streamlit 앱(trader 8탭 / trader-fund 4탭) 공존 — 통합 뷰는 trader-fund 거주. 선택적 후속: trader 대시보드에 한 줄 포인터(범위 밖).

## 11. 산출물
- 변경: `scripts/fund_book.py`(추출), `dashboard/app.py`(탭 추가).
- 신규: `dashboard/fund_portfolio.py`, 테스트 2개.
- 문서 동반 갱신: `README.md` / `docs/`(대시보드 탭 설명) — 구현 시.
