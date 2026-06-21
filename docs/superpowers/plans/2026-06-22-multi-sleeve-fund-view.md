# 멀티 슬리브 펀드 통합 뷰 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** trader-fund 대시보드에 조립된 바벨 펀드 북(코어·헌트·모멘텀·리저브)을 보여주는 `💹 펀드 포트폴리오` 탭 1개를 추가한다.

**Architecture:** `scripts/fund_book.py`의 `main()` 조립 로직을 순수 함수 `build_fund_book()`로 추출(behavior-preserving)해 CLI와 대시보드가 공유한다. 대시보드는 gitignore된 스냅샷을 `../trader/data/snapshots/`로 폴백 리졸브해 무인자로 조립하고, FundBook을 슬리브 막대 + 종목표 + 섹터 + OOS 존재성으로 렌더한다.

**Tech Stack:** Python 3.12, Streamlit, pandas, 기존 `engine/fund_book.py` · `engine/fund_exposure.py` · `engine/fund_book_oos.py`.

테스트 실행: `cd "/Users/jjuni/재무관리 모델/trader-fund" && .venv/bin/python -m pytest <경로> -v` (풀 스위트 직접 실행 금지 — exit 144).

---

## 파일 구조

| 파일 | 책임 | 변경 |
|------|------|------|
| `scripts/fund_book.py` | 바벨 조립 CLI + 재사용 함수 | Modify: `build_fund_book()` 추출, `main()`이 호출 |
| `dashboard/fund_portfolio.py` | 스냅샷 리졸버 + 캐시 조립 + 렌더 | Create |
| `dashboard/app.py` | Streamlit 탭 등록 | Modify: 4번째 탭 |
| `tests/test_fund_book_build.py` | `build_fund_book` 동작 + CLI 스모크 | Create |
| `tests/test_fund_portfolio.py` | 리졸버 + 캐시 데이터 함수 헤드리스 | Create |
| `README.md` / `docs/` | 대시보드 탭 설명 | Modify |

각 태스크는 독립적으로 빌드·테스트 가능. 데이터 경로는 `TRADER_SNAP = Path("/Users/jjuni/재무관리 모델/trader/data/snapshots")`를 테스트에서 사용.

---

## Task 1: `build_fund_book()` 추출 (behavior-preserving)

**Files:**
- Modify: `scripts/fund_book.py` (import 1줄 + 함수 추출 + `main()` 본문 교체)
- Test: `tests/test_fund_book_build.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_fund_book_build.py`:
```python
from pathlib import Path

from engine.fund_book import FundBook
from scripts.fund_book import build_fund_book

TRADER_SNAP = Path("/Users/jjuni/재무관리 모델/trader/data/snapshots")


def test_build_fund_book_core_hunt_only():
    book, sectors = build_fund_book(
        snapshot=TRADER_SNAP / "fundamentals-2026-06-01-gp2.csv",
        prices=TRADER_SNAP / "prices-2026-06-01.csv",
    )
    assert isinstance(book, FundBook)
    assert isinstance(sectors, dict)
    # core 슬리브는 항상 존재, fraction 0.35
    fracs = dict(book.sleeve_fractions)
    assert fracs.get("core") == 0.35
    # momentum off → invested 는 core(+hunt) 만; core 가 35% 채우면 ~0.35
    assert 0.25 <= book.invested <= 0.36
    assert all(0.0 <= p.fund_weight <= book.max_name_weight + 1e-9 for p in book.positions)


def test_build_fund_book_with_momentum():
    book, _ = build_fund_book(
        snapshot=TRADER_SNAP / "fundamentals-2026-06-01-gp2.csv",
        prices=TRADER_SNAP / "prices-2026-06-01.csv",
        price_history=TRADER_SNAP / "prices-ideal-2026-06-01.csv",
        momentum_snapshot=TRADER_SNAP / "fundamentals-2026-06-01-gp.csv",
    )
    fracs = dict(book.sleeve_fractions)
    assert fracs.get("momentum") == 0.25
    # core 35 + momentum 25 = 60% (hunt 비어도)
    assert 0.55 <= book.invested <= 0.61
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_fund_book_build.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_fund_book' from 'scripts.fund_book'`

- [ ] **Step 3: `build_fund_book()` 추출**

`scripts/fund_book.py` 상단 import 수정 (line 16):
```python
from datetime import date, datetime
```

`load_momentum_universe()` 정의 다음(현재 line 51 `def main` 직전)에 함수 삽입:
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
    price_history: Path | None = None,
    momentum_snapshot: Path | None = None,
    momentum_universe: Path | None = None,
    momentum_top_n: int = 7,
    momentum_cap: float = 0.20,
) -> tuple[FundBook, dict[str, str]]:
    """단일 PIT as_of에서 바벨(core+hunt+optional momentum)을 조립해 (FundBook, sectors) 반환.

    CLI(main)와 대시보드가 공유하는 단일 출처. 동작은 기존 main()과 동일 — 검증 슬리브
    타겟을 사용자 바벨 정책 비중으로 조립만 하고 펀드레벨 가드(종목캡·Σ비중≤1·롱온리)만 강제.
    """
    common = {
        "snapshot": snapshot,
        "prices": prices,
        "universe_csv": universe_csv,
        "sectors_csv": sectors_csv,
    }
    universe, sectors, effective = build_universe(as_of=as_of, **common)
    core = select_core_basket(universe, sectors=sectors, as_of=effective)
    core_weights = {h.symbol: h.weight for h in core.holdings}

    insider_signals, capital_signals, hunt_universe, _sec, _eff = build_hunt_inputs(
        catalog=MarketDataCatalog(db), as_of=effective, **common
    )
    hunt = select_hunt_basket(
        insider_signals,
        hunt_universe,
        capital_signals=capital_signals,
        sectors=sectors,
        as_of=effective,
    )
    hunt_weights = {h.symbol: h.weight for h in hunt.holdings}

    sleeves = [
        SleeveTarget("core", core_fraction, core_weights),
        SleeveTarget("hunt", hunt_fraction, hunt_weights),
    ]

    if price_history:
        momentum_syms = load_momentum_universe(momentum_universe)
        px = read_price_snapshot(price_history, verify=True)
        fund_cache = prefetch(MarketDataCatalog(db), snapshot_path=momentum_snapshot)
        as_of_dt = datetime.combine(effective, datetime.max.time())
        fund_by_sym = {}
        for sym in momentum_syms:
            rec = lookup_pit(fund_cache.get(sym, []), as_of_dt)
            if rec is not None:
                fund_by_sym[sym.upper()] = rec
        momentum = select_momentum_basket(
            px,
            fund_by_sym,
            momentum_syms,
            as_of=effective,
            top_n=momentum_top_n,
            cap=momentum_cap,
        )
        sleeves.append(momentum_sleeve_target(momentum, fraction=momentum_fraction))

    book = assemble_fund_book(sleeves, max_name_weight=max_name_weight)
    return book, sectors
```

`FundBook` import 추가 (line 25 수정):
```python
from engine.fund_book import FundBook, SleeveTarget, assemble_fund_book, format_fund_book  # noqa: E402
```

`main()` 본문의 `try:` 블록(현재 line 99~153, `# Core sleeve.` ~ `book = assemble_fund_book(...)`)을 아래로 교체:
```python
    try:
        if args.price_history and args.momentum_snapshot is None:
            print(
                "⚠️  momentum running off the LIVE catalog (NOT reproducible) — "
                "pass --momentum-snapshot to pin fundamentals",
                file=sys.stderr,
            )
        book, sectors = build_fund_book(
            as_of=as_of,
            snapshot=args.snapshot,
            prices=args.prices,
            universe_csv=args.universe_csv,
            sectors_csv=args.sectors_csv,
            db=args.db,
            core_fraction=args.core_fraction,
            hunt_fraction=args.hunt_fraction,
            momentum_fraction=args.momentum_fraction,
            max_name_weight=args.max_name_weight,
            price_history=args.price_history,
            momentum_snapshot=args.momentum_snapshot,
            momentum_universe=args.momentum_universe,
            momentum_top_n=args.momentum_top_n,
            momentum_cap=args.momentum_cap,
        )
    except (ValueError, FileNotFoundError) as e:
        raise SystemExit(str(e)) from e

    print(format_fund_book(book))
    if args.exposure:
        print()
        print(format_exposure(compute_exposure(book, sectors)))
    return 0
```
(주: `as_of = datetime.fromisoformat(args.as_of).date() if args.as_of else None` 줄은 try 위에 그대로 유지.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_fund_book_build.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: CLI 동작 보존 스모크**

Run:
```bash
.venv/bin/python scripts/fund_book.py \
  --snapshot "/Users/jjuni/재무관리 모델/trader/data/snapshots/fundamentals-2026-06-01-gp2.csv" \
  --prices "/Users/jjuni/재무관리 모델/trader/data/snapshots/prices-2026-06-01.csv" 2>&1 | head -5
```
Expected: `펀드 북 (50/50 바벨 조립 ...)` 헤더 + `sleeves: core=35% hunt=15% ...` 출력(리팩토링 전과 동일).

- [ ] **Step 6: 커밋**

```bash
git add scripts/fund_book.py tests/test_fund_book_build.py
git commit -m "refactor: scripts/fund_book main() 조립 로직을 build_fund_book() 함수로 추출

CLI 동작 보존. 대시보드 재사용을 위한 단일 출처. CLI 스모크 + 단위테스트 2건.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 스냅샷 리졸버 + 캐시 조립 데이터 함수

**Files:**
- Create: `dashboard/fund_portfolio.py`
- Test: `tests/test_fund_portfolio.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_fund_portfolio.py`:
```python
from pathlib import Path

from dashboard.fund_portfolio import resolve_snapshot, fund_book_payload

ROOT = Path("/Users/jjuni/재무관리 모델/trader-fund")


def test_resolve_snapshot_falls_back_to_trader():
    # trader-fund 로컬엔 gitignore로 없음 → ../trader/data/snapshots 폴백
    p = resolve_snapshot(["fundamentals-*-gp2.csv"], ROOT)
    assert p is not None
    assert p.name.endswith("-gp2.csv")
    assert p.exists()


def test_resolve_snapshot_missing_returns_none():
    assert resolve_snapshot(["zzz-does-not-exist-*.csv"], ROOT) is None


def test_fund_book_payload_shape_with_momentum():
    payload = fund_book_payload(ROOT, momentum_on=True)
    meta = payload["meta"]
    assert meta["available"] is True
    assert dict(meta["sleeve_fractions"]).get("core") == 0.35
    assert 0.55 <= meta["invested"] <= 0.61      # core35 + momentum25
    assert payload["positions"], "should have positions"
    row = payload["positions"][0]
    assert set(row) >= {"종목", "펀드%", "캡", "출처슬리브"}
    assert isinstance(payload["sectors"], list)
    assert payload["oos"]["n_entries"] == 0       # 원장 미생성


def test_fund_book_payload_missing_snapshots_graceful(tmp_path):
    payload = fund_book_payload(tmp_path, momentum_on=True)  # 빈 디렉토리
    assert payload["meta"]["available"] is False
    assert "스냅샷" in payload["meta"]["message"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_fund_portfolio.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.fund_portfolio'`

- [ ] **Step 3: `dashboard/fund_portfolio.py` 작성**

```python
"""펀드 포트폴리오 탭 — 조립된 바벨 FundBook 을 노출.

새 펀드 로직 없음: scripts.fund_book.build_fund_book(검증 조립기)의 결과를 렌더한다.
스냅샷 CSV 는 trader-fund 에서 gitignore 되므로 ../trader/data/snapshots 로 폴백 리졸브.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

# 모멘텀 기본 ON 에 필요한 스냅샷 (글롭 패턴 — 날짜 변경에 견고)
_FUND_SNAPSHOT = ["fundamentals-*-gp2.csv"]       # core/hunt 횡단 펀더멘털
_FUND_PRICES = ["prices-2*.csv"]                   # core/hunt 횡단 가격 (prices-ideal 제외)
_MOM_HISTORY = ["prices-ideal-*.csv"]              # 모멘텀 시계열
_MOM_SNAPSHOT = ["fundamentals-*-gp.csv"]          # 모멘텀 megacap 펀더멘털
_OOS_LEDGER = "fund-book-oos.jsonl"


def resolve_snapshot(patterns: list[str], root: Path) -> Path | None:
    """패턴에 맞는 최신 스냅샷 CSV 를 로컬→../trader 순으로 찾는다(ISO 날짜명=사전순 최신)."""
    for base in (root / "data" / "snapshots", root.parent / "trader" / "data" / "snapshots"):
        hits: list[Path] = []
        for pat in patterns:
            hits.extend(base.glob(pat))
        if hits:
            return max(hits, key=lambda p: p.name)
    return None


def _provenance(contributions: tuple[tuple[str, float], ...]) -> str:
    return "+".join(name for name, _ in contributions) or "—"


@st.cache_data(show_spinner=False)
def fund_book_payload(root: Path, *, momentum_on: bool = True) -> dict:
    """조립된 FundBook 을 plain dict 로 (st.cache_data 피클 안전). 스냅샷 없으면 available=False."""
    from scripts.fund_book import build_fund_book  # 무거운 import(yfinance) 지연
    from engine.fund_exposure import compute_exposure
    from engine.fund_book_oos import load_ledger

    snapshot = resolve_snapshot(_FUND_SNAPSHOT, root)
    prices = resolve_snapshot(_FUND_PRICES, root)
    if snapshot is None or prices is None:
        return {
            "meta": {
                "available": False,
                "message": (
                    "스냅샷 CSV 를 찾지 못했습니다(trader-fund 는 gitignore, "
                    "../trader/data/snapshots 도 없음). "
                    "scripts/snapshot_fundamentals.py / snapshot_prices.py 로 재생성하세요."
                ),
            },
            "positions": [],
            "sectors": [],
            "sleeve_attr": [],
            "oos": {"n_entries": 0, "note": ""},
        }

    kwargs: dict = {"snapshot": snapshot, "prices": prices}
    mom_hist = resolve_snapshot(_MOM_HISTORY, root) if momentum_on else None
    mom_snap = resolve_snapshot(_MOM_SNAPSHOT, root) if momentum_on else None
    if mom_hist is not None and mom_snap is not None:
        kwargs["price_history"] = mom_hist
        kwargs["momentum_snapshot"] = mom_snap

    book, sectors = build_fund_book(**kwargs)
    exposure = compute_exposure(book, sectors)

    positions = [
        {
            "종목": p.symbol,
            "펀드%": round(p.fund_weight * 100, 2),
            "캡": "★" if p.capped else "",
            "출처슬리브": _provenance(p.contributions),
        }
        for p in book.positions
    ]
    sector_rows = [
        {"섹터": s.sector, "비중%": round(s.weight * 100, 2), "종목수": s.n_names}
        for s in exposure.sector_exposures
    ]
    sleeve_attr = [
        {"슬리브": a.sleeve, "기여%": round(a.weight * 100, 2)}
        for a in exposure.sleeve_attribution
    ]

    ledger_path = root / "out" / _OOS_LEDGER
    entries = load_ledger(ledger_path) if ledger_path.exists() else []

    return {
        "meta": {
            "available": True,
            "momentum_on": "price_history" in kwargs,
            "invested": round(book.invested, 4),
            "reserve_cash": round(book.reserve_cash, 4),
            "max_name_weight": book.max_name_weight,
            "n_positions": book.n_positions,
            "effective_n": round(exposure.effective_n, 1),
            "top_name": exposure.top_name,
            "top_name_weight": round(exposure.top_name_weight, 4),
            "sleeve_fractions": list(book.sleeve_fractions),
        },
        "positions": positions,
        "sectors": sector_rows,
        "sleeve_attr": sleeve_attr,
        "oos": {
            "n_entries": len(entries),
            "latest_rebal": entries[-1].rebal_date if entries else None,
        },
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_fund_portfolio.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add dashboard/fund_portfolio.py tests/test_fund_portfolio.py
git commit -m "feat: 펀드 포트폴리오 데이터 레이어 (스냅샷 리졸버 + 캐시 조립)

build_fund_book 을 trader 스냅샷 폴백으로 호출해 plain dict 페이로드 반환.
모멘텀 기본 ON(글롭 리졸브), 스냅샷 없으면 graceful. 헤드리스 테스트 4건.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 렌더 함수 `render_fund_portfolio()`

**Files:**
- Modify: `dashboard/fund_portfolio.py` (렌더 함수 추가)

렌더는 Streamlit UI 라 단위테스트 안 함(Task 2 데이터 함수까지 검증). 수동 스모크로 확인.

- [ ] **Step 1: 렌더 함수 추가**

`dashboard/fund_portfolio.py` 끝에 추가:
```python
_HONESTY = (
    "정직성: 알파 주장 없음 · core/hunt 검증 엣지 없음 · momentum 만 검증"
    "(+8.15%/yr walk-forward, US 한정) · 리스크 모델 아님(서술적 진단)."
)


def render_fund_portfolio(root: Path) -> None:
    st.subheader("펀드 포트폴리오 — 조립된 50/50 바벨")
    c1, c2 = st.columns([1, 4])
    momentum_on = c1.checkbox("모멘텀 슬리브 포함", value=True, key="fund_mom")
    if c2.button("↻ 새로고침", key="fund_refresh"):
        fund_book_payload.clear()

    try:
        payload = fund_book_payload(root, momentum_on=momentum_on)
    except Exception as e:  # 조립 실패 — 탭만 죽고 앱은 유지
        st.error(f"펀드 북 조립 실패: {e}")
        return

    meta = payload["meta"]
    if not meta["available"]:
        st.warning(meta["message"])
        return

    st.caption(_HONESTY)

    # 상단 — 슬리브 배분 (정책 타겟 vs 실현)
    fracs = dict(meta["sleeve_fractions"])
    reserve_policy = round(1.0 - sum(fracs.values()), 4)
    bar = {**{k: v for k, v in fracs.items()}, "reserve": reserve_policy}
    st.markdown("**정책 타겟 (슬리브 비중)**")
    st.bar_chart(pd.DataFrame({"비중": bar}))
    m = st.columns(5)
    m[0].metric("invested(실현)", f"{meta['invested'] * 100:.1f}%")
    m[1].metric("reserve(실현)", f"{meta['reserve_cash'] * 100:.1f}%")
    m[2].metric("종목수", meta["n_positions"])
    m[3].metric("유효종목수", meta["effective_n"])
    top = meta["top_name"] or "—"
    m[4].metric("top", f"{top} {meta['top_name_weight'] * 100:.1f}%")
    if not meta["momentum_on"]:
        st.info("모멘텀 슬리브 제외(토글 OFF 또는 시계열 스냅샷 없음) — core+hunt 만 표시.")

    # 중단 — 종목 / 섹터
    cols = st.columns([3, 2])
    with cols[0]:
        st.markdown("**종목 (펀드 비중 · 출처 슬리브)**")
        st.dataframe(pd.DataFrame(payload["positions"]), use_container_width=True, hide_index=True)
    with cols[1]:
        st.markdown("**섹터 익스포저**")
        st.dataframe(pd.DataFrame(payload["sectors"]), use_container_width=True, hide_index=True)
        st.markdown("**슬리브 기여**")
        st.dataframe(pd.DataFrame(payload["sleeve_attr"]), use_container_width=True, hide_index=True)

    # 하단 — OOS 성과
    st.markdown("**포워드 OOS 성과**")
    oos = payload["oos"]
    if oos["n_entries"] == 0:
        st.info(
            "포워드 OOS 원장 미가동(표본 0). PIT 기록을 시작하려면 "
            "`scripts/fund_book_oos.py` 로 리밸 시점 펀드 북을 등록하세요."
        )
    else:
        st.caption(
            f"원장 {oos['n_entries']}건 · 최근 리밸 {oos['latest_rebal']} "
            "(누적/연환산 초과·적중률·excess Sharpe 채점은 mark price history 필요 — 후속)"
        )
```

- [ ] **Step 2: import 확인 (런타임 안전)**

Run: `.venv/bin/python -c "import ast; ast.parse(open('dashboard/fund_portfolio.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: 커밋**

```bash
git add dashboard/fund_portfolio.py
git commit -m "feat: 펀드 포트폴리오 렌더 — 슬리브 막대 + 종목/섹터 + OOS 존재성

정책타겟 vs 실현 구분, 출처슬리브 표기, 정직성 캡션. OOS 원장 미가동시 정직 안내.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 대시보드 탭 배선

**Files:**
- Modify: `dashboard/app.py`

- [ ] **Step 1: import 추가**

`dashboard/app.py` line 9 다음에 추가:
```python
from dashboard.fund_portfolio import render_fund_portfolio
```

- [ ] **Step 2: 탭 등록 + 디스패치**

line 21 교체:
```python
    overview, screen, valuation, fund = st.tabs(
        ["Catalog", "Momentum", "Valuation", "💹 펀드 포트폴리오"]
    )
```

`with valuation:` 블록(현재 line 43~52) 다음에 추가:
```python
    with fund:
        render_fund_portfolio(ROOT)
```

- [ ] **Step 3: 문법 + import 무결성 확인**

Run: `.venv/bin/python -c "import ast; ast.parse(open('dashboard/app.py').read()); print('syntax OK')"`
Expected: `syntax OK`

Run: `.venv/bin/python -c "from dashboard.app import main; print('import OK')"`
Expected: `import OK`

- [ ] **Step 4: 헤드리스 렌더-데이터 최종 확인**

Run:
```bash
.venv/bin/python -c "
from pathlib import Path
from dashboard.fund_portfolio import fund_book_payload
p = fund_book_payload(Path('/Users/jjuni/재무관리 모델/trader-fund'), momentum_on=True)
m = p['meta']
print('available', m['available'], '| invested', m['invested'], '| n', m['n_positions'], '| picks', len(p['positions']), '| oos', p['oos']['n_entries'])
"
```
Expected: `available True | invested 0.6 | n 20 | picks 20 | oos 0`

- [ ] **Step 5: 커밋**

```bash
git add dashboard/app.py
git commit -m "feat: 대시보드에 💹 펀드 포트폴리오 탭 배선 (3→4탭)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 검증 + 문서 + Codex 리뷰

**Files:**
- Modify: `README.md`, `docs/` (대시보드 탭 설명이 있으면)

- [ ] **Step 1: 타깃 테스트 일괄**

Run: `.venv/bin/python -m pytest tests/test_fund_book_build.py tests/test_fund_portfolio.py -v`
Expected: 6 passed

- [ ] **Step 2: 문서 갱신**

`README.md` 의 대시보드 실행 설명 근처(또는 `docs/`)에 추가:
```markdown
대시보드 `💹 펀드 포트폴리오` 탭은 `build_fund_book`(검증 조립기)으로 50/50 바벨을
조립해 슬리브 비중(core/hunt/momentum/reserve) · 종목별 펀드비중+출처슬리브 · 섹터
익스포저 · 포워드 OOS 성과를 한 화면에 보여준다. 스냅샷은 ../trader/data/snapshots
폴백으로 로드(재현 가능). 정직성: momentum 만 검증 엣지, core/hunt 는 알파 주장 없음.
```

- [ ] **Step 3: Codex 리뷰 (필수)**

Run: `codex review --uncommitted`
P1/P2 발견 시 수정 후 재실행. 블로킹 없을 때까지.

- [ ] **Step 4: 문서 커밋**

```bash
git add README.md docs/
git commit -m "docs: 펀드 포트폴리오 탭 설명 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 검증 체크리스트 (스펙 커버리지)

- [x] 목표배분: 슬리브 막대 + 종목표(펀드%/캡/출처) + 섹터 — Task 3
- [x] OOS 성과(표본0 정직표기) — Task 3
- [x] assemble_fund_book 재사용(추출) — Task 1
- [x] 스냅샷 trader 폴백 리졸버 — Task 2
- [x] 모멘텀 기본 ON, 없으면 graceful — Task 2/3
- [x] 정직성 캡션 — Task 3
- [x] 위치 = trader-fund 대시보드 새 탭 — Task 4
- [x] CLI 패리티 보존 — Task 1
- [x] 테스트(빌드+데이터 함수) — Task 1/2
- [x] 문서 동반 갱신 — Task 5
