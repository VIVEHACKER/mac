# 차트 읽기 정본 (Chart Reading Canon) — 탐지 알고리즘 명세

## 철학

차트는 OHLCV+호가(L2)+미체결약정(OI)의 렌더링이며, 우리는 그것을 사람 눈이 아니라 알고리즘으로 읽어 진입 타이밍을 결정한다. 모든 탐지는 백테스트·재현·시그널 연결이 가능해야 한다. 개념의 해석 여지를 최소화하고 PriceBar 시퀀스와 크립토 전용 보조 입력을 stdlib(statistics, math)만으로 연산하여 불리언 또는 수치 시그널을 반환하는 순수 함수로 구현한다. 추상적인 "패턴 인식"은 재현 불가능한 코드로 기록되는 순간 기술 부채가 된다는 사실을 전제한다.

---

## 데이터 입력 계약

### PriceBar

```python
@dataclass
class PriceBar:
    symbol: str          # 종목/심볼 (e.g. 'BTC/USDT', 'AAPL')
    market: str          # 거래소 또는 시장 (e.g. 'binance', 'nasdaq')
    ts: date             # 봉 기준 날짜/시각 (ISO date)
    open: float
    high: float
    low: float
    close: float
    volume: float
    freq: str            # '1d' | '4h' | '1h' | '15m' 등
    currency: str        # 기준 통화 (e.g. 'USDT', 'USD', 'KRW')
    source: str          # 데이터 출처 식별자
```

탐지기 함수는 `list[PriceBar]`를 시간 오름차순으로 받는다. 최신 봉은 `bars[-1]`이다.

### 크립토 전용 보조 입력

**호가 스냅샷 (Order Book Snapshot)**  
`bids: list[[price, size]]` / `asks: list[[price, size]]` — 각 리스트는 가격 내림차순(bids)/오름차순(asks)으로 정렬된 L2 사다리. ccxt `fetch_order_book()` 응답에서 직접 전달한다.

**미체결약정 시계열 (Open Interest History)**  
`list[dict]` — 각 원소: `{ts: date, oi: float, mark_price: float | None}`. ccxt `fetch_open_interest_history()` 응답 기준. `mark_price`는 선택 필드이며 없을 경우 `None`.

**펀딩비 (Funding Rate)**  
`list[dict]` — 각 원소: `{ts: date, funding_rate: float}`. ccxt `fetch_funding_rate_history()` 기준. 영구 선물 거래소 전용.

### 개념별 필요 입력 매트릭스

| 개념 | ohlcv_daily | ohlcv_intraday | multi_timeframe | order_book | open_interest | funding |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| 1. 시장구조 (Market Structure) | ✅ | ✅ | ✅ | — | — | — |
| 2. FVG (Fair Value Gap) | ✅ | ✅ | ✅ | — | — | — |
| 3. 오더블록 (Order Block) | ✅ | ✅ | ✅ | — | — | — |
| 4. 유동성 (Liquidity) | ✅ | ✅ | ✅ | ✅ | — | — |
| 5. 매물대 (Supply & Demand Zone) | ✅ | ✅ | ✅ | — | — | — |
| 6. 볼륨 분석 (Volume Analysis) | ✅ | ✅ | — | ✅ | — | — |
| 7. 와이코프 / 매집 (Wyckoff / Accumulation) | ✅ | ✅ | — | ✅ | — | — |
| 8. 차트 패턴 (Chart Patterns) | ✅ | ✅ | ✅ | — | — | — |
| 9. 캔들 패턴 (Candle Patterns) | ✅ | ✅ | — | — | — | — |
| 10. 호가 (Order Book Depth) | — | — | — | ✅ | — | — |
| 11. 미체결약정 / 펀딩 (OI & Funding) | — | ✅ | — | — | ✅ | ✅ |
| 컨플루언스 프레임워크 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

범례: ✅ 필수 / — 불필요(있으면 무시)

---

## 목차

| 번호 | 개념 | 파일 | 구현 모듈 |
|---|---|---|---|
| 1 | **시장구조 (Market Structure)** — 고점/저점 갱신, 추세 방향, CHoCH·BOS 탐지 | `01_market_structure.md` | `detectors/market_structure.py` |
| 2 | **FVG (Fair Value Gap)** — 3봉 불균형 공백, 미충전 구간 추적 | `02_fvg.md` | `detectors/fvg.py` |
| 3 | **오더블록 (Order Block)** — 기관 주문 흔적 봉, 방향 전환 이전 마지막 반대 봉 | `03_order_block.md` | `detectors/order_block.py` |
| 4 | **유동성 (Liquidity)** — 스윙 고저 군집, 동일 저항·지지 라인, 스탑 헌팅 구간 | `04_liquidity.md` | `detectors/liquidity.py` |
| 5 | **매물대 (Supply & Demand Zone)** — 폭발적 이탈 직전 횡보 구간, 강도 점수 | `05_supply_demand.md` | `detectors/supply_demand.py` |
| 6 | **볼륨 분석 (Volume Analysis)** — 상대 볼륨, 볼륨 클러스터, 흡수 감지 | `06_volume.md` | `detectors/volume.py` |
| 7 | **와이코프 / 매집 (Wyckoff / Accumulation)** — PS·SC·AR·ST·스프링·SOS 시퀀스 | `07_wyckoff.md` | `detectors/wyckoff.py` |
| 8 | **차트 패턴 (Chart Patterns)** — 헤드앤숄더, 이중 고저, 웨지, 플래그, 컵핸들 | `08_chart_patterns.md` | `detectors/chart_patterns.py` |
| 9 | **캔들 패턴 (Candle Patterns)** — 핀바, 엔걸핑, 도지, 해머, 슈팅스타 등 | `09_candle_patterns.md` | `detectors/candle_patterns.py` |
| 10 | **호가 (Order Book Depth)** — 벽(Wall) 탐지, 흡수 비율, 스프레드 이상 | `10_order_book.md` | `detectors/order_book.py` |
| 11 | **미체결약정 / 펀딩 (OI & Funding)** — OI 발산·수렴, 펀딩비 극단값, 청산 밀집 | `11_oi_funding.md` | `detectors/oi_funding.py` |
| — | **컨플루언스 프레임워크** — 다중 시그널 결합, 진입 조건 합성, 신뢰도 점수 | `12_confluence.md` | `detectors/confluence.py` |

---

> 각 파일은 (1) 개념 정의, (2) 탐지 알고리즘 의사코드, (3) 순수 Python 구현, (4) 엣지 케이스, (5) 백테스트 연결 포인트 순서로 작성한다.

## 1. 시장구조 (market_structure)

**정의** — 시장구조(Market Structure)는 특정 타임프레임에서 가격이 순차적으로 찍어가는 확정된 스윙 고점(swing high)과 스윙 저점(swing low)의 계층적 흐름을 기술하며, 각 확정된 피벗(pivot)을 직전 동종(同種) 확정 피벗 대비 Higher High(HH), Higher Low(HL), Lower High(LH), Lower Low(LL)로 분류한다. Break of Structure(BOS)는 현재 구조적 추세 방향의 가장 최근 확정된 스윙 극단을 캔들 종가(candle close)가 돌파하는 것으로(상승 추세에서 마지막 확정 스윙 고점 상방 종가 돌파; 하락 추세에서 마지막 확정 스윙 저점 하방 종가 돌파), 추세 지속을 확인한다. Change of Character(CHoCH)는 현재 추세에 역행하는 구조적 피벗을 캔들 종가가 돌파하는 것으로(상승 추세에서 가장 최근 확정 Higher Low 하방 종가 돌파; 하락 추세에서 가장 최근 확정 Lower High 상방 종가 돌파), 기존 추세 편향의 잠재적 반전을 시사한다. 스윙(외부) 구조는 주요 기관적 극단(institutional extremes)을 포착하는 넓은 피벗 룩백을 사용하고, 내부(internal) 구조는 진입 타이밍에 사용하는 서브-레그(sub-leg) 스윙을 추적하기 위한 짧은 룩백을 사용하며, 내부 신호는 절대 스윙-레벨 trend_bias를 뒤집는 데 사용해서는 안 된다. Equal Highs(EQH)와 Equal Lows(EQL)는 동종의 확정된 스윙 피벗 두 개 이상이 퍼센트-가격 허용 범위 내에서 같은 가격대에 형성될 때 발생하며, 그 위아래에 대기 중인 스탑 주문이 집적된 유동성 풀(liquidity pool)을 표시한다.

**탐지 알고리즘** —

1. **STEP 1 — 피벗 탐지 (swing_left, swing_right 파라미터 적용):** 인덱스 swing_left 부터 `len(bars) - swing_right - 1` 까지 바를 순회한다. 바 i에서, `bars[i].high`가 `[i-swing_left … i-1]` 및 `[i+1 … i+swing_right]` 범위 내 모든 j에 대해 `bars[j].high`보다 엄격히(strictly) 크면 스윙 고점 후보다. 마찬가지로 `bars[i].low`가 해당 범위 내 모든 j에 대해 `bars[j].low`보다 엄격히 작으면 스윙 저점 후보다. **타이 처리(tie-handling):** 인접 바에 동일한 고점(`bars[j].high == bars[i].high`)이 있으면 i는 스윙 고점이 아니다 — 엄격히-큰 규칙에 의해 평탄한 상단 시퀀스(flat-top sequence)에서는 피벗이 생성되지 않는다; 이를 명시적으로 문서화하여 하위 소비자가 동일-고점 구간에 레이블이 붙지 않음을 인지하도록 한다. 바 하나가 동시에 스윙 고점과 스윙 저점 모두에 해당하는 경우도 있다(마이크로 타임프레임 외에서는 드물다). 피벗은 처리 바 인덱스 `i + swing_right` 시점에만 확정(confirm)되어 출력 리스트에 추가된다 — 확정 지연(confirmation lag)이 발생한다. 해당 바 이전에는 후보를 하위 단계에 노출시켜서는 안 된다. 확정된 피벗을 저장: `pivot_highs = [(bar_index=i, price=bars[i].high, ts=bars[i].ts, confirmed_at=i+swing_right)]`, `pivot_lows = [(bar_index=i, price=bars[i].low, ts=bars[i].ts, confirmed_at=i+swing_right)]`.

2. **STEP 2 — 내부 구조 피벗:** STEP 1을 `swing_left=internal_left`, `swing_right=internal_right`(기본값: 3, 3)로 반복하여 `int_pivot_highs`와 `int_pivot_lows`를 생성한다. 스윙 구조와 내부 구조 피벗 리스트는 이후 모든 단계에서 완전히 분리 유지한다. 내부 피벗은 진입 타이밍 신호(`internal_CHoCH`, `internal_BOS`)에만 영향을 주며, 절대 `trend_bias`를 갱신해서는 안 된다.

3. **STEP 3 — 스윙 피벗 레이블 부여 (HH/LH 고점; HL/LL/EQH/EQL 저점 및 고점):** `prev_sh_price`(가장 최근 레이블된 확정 스윙 고점 가격)와 `prev_sl_price`(가장 최근 레이블된 확정 스윙 저점 가격)를 유지하며, 각각 첫 번째 확정 피벗 가격으로 초기화한다. 새로운 확정 스윙 고점 가격 `p_h`에 대해: (a) `abs(p_h - prev_sh_price) / max(p_h, prev_sh_price) <= eq_threshold`이면 → `'EQH'` 레이블 부여(STEP 4의 HH/HL/LH/LL 추세 투표에서 제외하여 STEP 8 클러스터 스캔과 이중 계산 방지); (b) `p_h > prev_sh_price`이면 → `'HH'`; (c) 그 외 → `'LH'`. 세 경우 모두 `prev_sh_price = p_h` 설정. 새로운 확정 스윙 저점 가격 `p_l`에 대해: (a) `abs(p_l - prev_sl_price) / max(p_l, prev_sl_price) <= eq_threshold`이면 → `'EQL'`; (b) `p_l < prev_sl_price`이면 → `'LL'`; (c) 그 외 → `'HL'`. 그 후 `prev_sl_price = p_l` 설정. 내부 피벗에도 독립된 `prev_int_sh_price` / `prev_int_sl_price` 트래커를 사용하여 동일 로직 적용. **분모 주의:** eq_threshold 비교 시 항상 `max(p_h, prev_sh_price)`로 나눠 피벗 도착 순서에 무관하게 대칭성을 보장한다.

4. **STEP 4 — 추세 분류 (결정론적 2-피벗 규칙 — 캐노니컬 SMC 정의):** `trend_bias`는 가장 최근 확정된 스윙 고점 2개와 스윙 저점 2개의 관계로 결정된다. SH1 = 가장 최근 확정 스윙 고점, SH2 = 두 번째 최근 확정 스윙 고점, SL1 = 가장 최근 확정 스윙 저점, SL2 = 두 번째 최근 확정 스윙 저점. **BULLISH:** `SH1.price > SH2.price AND SL1.price > SL2.price` (HH + HL 패턴). **BEARISH:** `SH1.price < SH2.price AND SL1.price < SL2.price` (LH + LL 패턴). 그 외: **RANGING**. 확정된 스윙 고점 2개와 스윙 저점 2개가 모두 존재할 때까지 `trend_bias = 'RANGING'`으로 초기화. EQH/EQL 레이블 피벗(STEP 3)은 이 비교에서 제외 — HH/HL/LH/LL 레이블 피벗만 사용. 라이브 프로덕션에서 롤링-투표 윈도우를 사용하지 말 것 — 캐노니컬 2-피벗 정의와 괴리되며 `trend_lookback` 파라미터 선택에 대한 불필요한 민감도를 도입한다.

5. **STEP 5 — BOS 탐지 (Break of Structure — 추세 지속):** `last_unbroken_swing_high`(아직 어떤 바의 종가에도 돌파되지 않은 가장 최근 확정 스윙 고점을 가리키는 포인터)와 `last_unbroken_swing_low`(아직 종가 하방 돌파가 없는 가장 최근 확정 스윙 저점 포인터)를 유지한다. 새 바 b(완전히 종가가 확정된 바만 사용)마다: **Bullish BOS** — `trend_bias == 'BULLISH' AND b.close > last_unbroken_swing_high.price`이면 → BOS 이벤트 기록 `{ts: b.ts, direction: 'BULLISH', level: last_unbroken_swing_high.price, bar_index: current_bar_index, pivot_bar_index: last_unbroken_swing_high.bar_index, type: 'swing_BOS', strength: (b.close - last_unbroken_swing_high.price) / last_unbroken_swing_high.price}`. **중요:** 즉시 `last_unbroken_swing_high`를 다음 확정 스윙 고점(이미 확정된 것이 있으면)으로 전진시키거나, 없으면 STEP 1을 통해 다음 확정을 기다린다 — 동일 레벨에서 이후 모든 바에 BOS가 재트리거되지 않도록 한다. **Bearish BOS** — 대칭: `trend_bias == 'BEARISH' AND b.close < last_unbroken_swing_low.price`이면 → bearish BOS 기록 후 `last_unbroken_swing_low` 전진. 내부 피벗에도 동일 로직 적용 → `type='internal_BOS'`. 윅(wick)만 돌파(`b.high > level`이지만 `b.close < level`; bearish의 경우 `b.low < level`이지만 `b.close >= level`)는 BOS가 아니며 `'liquidity_sweep'`으로 분류한다. `trend_bias == 'RANGING'`일 때는 BOS를 억제한다.

6. **STEP 6 — CHoCH 탐지 (Change of Character — 반전):** CHoCH는 `trend_bias`가 이미 `'BULLISH'` 또는 `'BEARISH'`일 때만(즉, `'RANGING'`이 아닐 때만) 발화한다. **BULLISH 추세에서:** `last_confirmed_HL`(가장 최근 `'HL'` 레이블 피벗을 가리키는 포인터)을 유지한다. `b.close < last_confirmed_HL.price`인 첫 번째 바 b에서 Bearish CHoCH 발화: `{ts: b.ts, direction: 'BEARISH', level: last_confirmed_HL.price, type: 'swing_CHoCH', bar_index: current_bar_index, pivot_bar_index: last_confirmed_HL.bar_index}` 기록. 발화 후 해당 HL을 `'consumed'`로 표시 — 이후 바들이 동일 레벨 아래로 종가가 형성되더라도 동일 HL 레벨에서 CHoCH를 재발화하지 않는다. `last_confirmed_HL` 포인터를 CHoCH 이후 형성되는 다음 HL로 갱신. **BEARISH 추세에서:** `b.close > last_confirmed_LH.price`이면 Bullish CHoCH 발화; 대칭 규칙 적용. 내부 피벗 → `type='internal_CHoCH'`. 윅 전용 규칙 적용: `b.close`가 레벨을 돌파해야 한다. **참고:** 여기서 CHoCH 레벨로 사용되는 HL은 STEP 3의 가장 최근 확정 HL이다 — 일부 대안적 SMC 구현은 직전 BOS 무브를 생성한 HL을 사용하는데, 이 변형은 CHoCH 이벤트가 적지만 확신도가 높다. 선택은 구현 시 고정되어야 한다.

7. **STEP 7 — BOS/CHoCH 이후 trend_bias 갱신:** BULLISH 방향 swing_BOS 발화 후 → `trend_bias` `'BULLISH'` 유지. BEARISH 방향 swing_CHoCH 발화 후 → `trend_bias = 'BEARISH'` 설정. BULLISH 방향 swing_CHoCH 발화 후 → `trend_bias = 'BULLISH'` 설정. CHoCH 후 다음 리셋을 수행: (a) `prev_sh_price`를 가장 최근 확정 스윙 HIGH 가격으로 리셋(CHoCH 확인 바의 종가가 아님 — 이는 레이블링 원점을 잘못 앵커링한다); (b) `prev_sl_price`를 가장 최근 확정 스윙 LOW 가격으로 리셋; (c) `last_unbroken_swing_high`와 `last_unbroken_swing_low` 포인터를 각 리스트의 가장 최근 확정 피벗으로 리셋. 이를 통해 이후 HH/HL/LH/LL 레이블과 BOS 체크가 CHoCH 바의 종가가 아닌 새 추세의 올바른 구조적 원점에 앵커링된다.

8. **STEP 8 — Equal Highs / Equal Lows (EQH / EQL) — 클러스터 스캔:** STEP 3의 레이블링 후, 마지막 `eqh_lookback` 바 내의 모든 확정 스윙 고점을 스캔한다. `abs(p_a - p_b) / max(p_a, p_b) <= eq_threshold`를 만족하는 것들을 그룹화(STEP 3과 일관되게 max 분모 사용). ≥ 2개 피벗 그룹은 EQH 클러스터를 형성: `{zone_high: max(그룹 가격들), zone_low: min(그룹 가격들), touch_count: len(그룹), ts_first: 가장 이른 ts, ts_last: 가장 최근 ts, type: 'EQH', mitigated: False}` 기록. 스윙 저점에도 동일 로직 → `'EQL'`. **중복 제거:** STEP 3에서 이미 `'EQH'`로 레이블된 피벗은 이 클러스터 스캔에 참여할 수 있지만, 두 번째 `'swing_pivot'` 이벤트를 emit하지 않는다; STEP 3 emission이 이미 피벗 단위 레코드를 처리하므로, STEP 8은 존(zone) 레벨 레코드만 emit한다. 이후 바의 `b.close`가 `zone_high`(EQH의 경우)를 엄격히 초과하거나 `zone_low`(EQL의 경우) 아래로 엄격히 종가가 형성될 때 `mitigated=True`로 표시한다.

9. **STEP 9 — 구조 레벨 추적 (진입 시스템 사용을 위해):** 다음 라이브 딕셔너리를 유지한다: `structure_levels = {'last_swing_high': {price, ts, label, bar_index}, 'last_swing_low': {price, ts, label, bar_index}, 'last_unbroken_swing_high': {price, ts, bar_index}, 'last_unbroken_swing_low': {price, ts, bar_index}, 'last_HL': {price, ts, bar_index}, 'last_LH': {price, ts, bar_index}, 'last_int_swing_high': {...}, 'last_int_swing_low': {...}, 'trend_bias': str, 'int_trend_bias': str}`. `'last_swing_high'`(BOS 상태 무관하게 가장 최근 확정 피벗)와 `'last_unbroken_swing_high'`(STEP 5의 BOS 트리거 레벨)의 구분에 주의한다. 두 가지 모두 필요하다: STEP 5는 unbroken 버전을 사용; 트레일링-스탑 로직은 구조적 버전을 사용.

10. **STEP 10 — 출력 조립:** 탐지된 각 이벤트에 대해 모든 output_fields가 채워진 레코드를 출력 리스트에 추가한다. `'swing_pivot'`, `'internal_pivot'`, `'swing_BOS'`, `'internal_BOS'`, `'swing_CHoCH'`, `'internal_CHoCH'`, `'EQH'`, `'EQL'`, `'liquidity_sweep'` 타입의 이벤트는 모두 별도 레코드로 emit된다. 각 레코드는 mitigated 상태를 포함하며(emit 시 False; 이후 바에서 가격이 레벨을 종가 돌파할 때 소급 갱신). 어떤 레코드도 미래 바 데이터를 참조해서는 안 된다 — 모든 필드는 `bars[0 … current_bar_index]`에서만 계산 가능해야 한다.

**파라미터** —

| name | default | 의미 |
|------|---------|------|
| `swing_left` | `5` | 스윙 구조 피벗 확정에 필요한 좌측 바 수. 값이 클수록 더 적고 유의미한 피벗이 생성된다. |
| `swing_right` | `5` | 스윙 구조 피벗 확정에 필요한 우측 바 수. 피벗이 잠금되기까지 `swing_right` 바의 확정 지연이 발생. 노이즈 감소를 위해 >= 2 권장. |
| `internal_left` | `3` | 내부 구조(서브-레그) 피벗 탐지의 좌측 룩백. `swing_left`보다 짧아 레그 내 스윙을 포착. |
| `internal_right` | `3` | 내부 구조 피벗 확정을 위한 우측 룩백. 노이즈 감소를 위해 >= 2 권장. |
| `eq_threshold` | `0.0015` | Equal Highs/Lows 판정 퍼센트 허용치: `abs(p1 - p2) / max(p1, p2) <= eq_threshold`이면 동일로 분류. 기본값 0.15%는 LuxAlgo EQH/EQL 구현 기준. 분모는 항상 `max(p1, p2)`를 사용하여 도착 순서에 무관한 대칭성을 보장. |
| `eqh_lookback` | `50` | EQH/EQL 클러스터 형성을 위해 동일 스윙 고점/저점을 탐색하는 최대 바 룩백 창. |
| `trend_lookback` | `6` | **라이브 사용에서 DEPRECATED** — 결정론적 2-피벗 규칙(STEP 4)이 캐노니컬. 이 파라미터는 선택적 롤링-투표 진단 모드에만 보존됨. 사용 시 최소 4 권장. |
| `use_body_close` | `True` | True이면 BOS/CHoCH에 `bar.close`가 레벨을 돌파해야 함(캐노니컬 ICT body-close 규칙). False이면 `bar.high` 또는 `bar.low` 돌파만으로 충분(wick-break 규칙). 기본값 True. |
| `min_displacement_pct` | `0.0` | 종가가 돌파 레벨을 초과해야 하는 최소 퍼센트(BOS/CHoCH 변위 필터). 0.0이면 비활성. 자주 되돌아오는 경계선 종가를 필터링하려면 예: 0.001 설정. |

**출력 필드** —

- `event_type: str` — `['swing_pivot', 'internal_pivot', 'swing_BOS', 'internal_BOS', 'swing_CHoCH', 'internal_CHoCH', 'EQH', 'EQL', 'liquidity_sweep']` 중 하나
- `ts: date` — 이벤트를 확정하거나 트리거한 바의 타임스탬프
- `direction: str` — `'BULLISH'` 또는 `'BEARISH'` (`trend_bias` 확립 전의 레이블 없는 피벗 이벤트에는 null)
- `level: float` — 돌파된 가격 레벨(BOS/CHoCH), 피벗 가격(피벗 이벤트), 또는 존 중간점(EQH/EQL)
- `zone_low: float` — EQH/EQL 클러스터 가격 범위 하단(비-존 이벤트는 null)
- `zone_high: float` — EQH/EQL 클러스터 가격 범위 상단(비-존 이벤트는 null)
- `label: str` — `HH`, `HL`, `LH`, `LL`, `EQH`, `EQL` (BOS/CHoCH/liquidity_sweep 이벤트는 null)
- `trend_bias: str` — 이벤트 emit 시점의 `BULLISH`/`BEARISH`/`RANGING` 스냅샷(소급 갱신 없음)
- `strength: float` — BOS/CHoCH: `abs(close - level) / level` 분수 초과분; EQH/EQL: `touch_count`를 float으로 캐스팅; 그 외 null
- `touch_count: int` — EQH/EQL 클러스터를 구성하는 피벗 수(비-클러스터 이벤트는 null)
- `mitigated: bool` — emit 시 False; 이후 바의 종가가 존 레벨을 돌파하면 True로 설정
- `bar_index: int` — 확인 바의 입력 `list[PriceBar]` 내 0-기반 인덱스(이벤트가 알고리즘에 알려진 바)
- `pivot_bar_index: int` — 실제 피벗 바의 인덱스(스윙 피벗의 경우 `= bar_index - swing_right`; BOS/CHoCH 이벤트의 경우 이벤트 바가 확인 바이므로 `bar_index`와 동일)
- `structure_scope: str` — `'swing'` 또는 `'internal'`

**진입 관련성** —

시장구조는 진입 타이밍의 GATE(진입 허용 여부)와 DIRECTION(방향) 레이어를 구동한다.

1. **방향(DIRECTION):** `trend_bias`와 일치하는 진입만 취한다. BULLISH 구조에서는 롱 진입만, BEARISH에서는 숏 진입만. RANGING 상태 → bias가 BULLISH 또는 BEARISH로 해소될 때까지 모든 방향성 진입을 보류.

2. **내부_CHoCH를 통한 타이밍:** 스윙 `trend_bias`에 역행하는 `internal_CHoCH`는 가장 이른 유효 진입 창을 표시한다 — 더 큰 추세의 되돌림(pullback) 내에서 서브-레그 반전이 형성 중임을, 즉 조정이 끝날 수 있음을 시사한다. 내부 구조 신호는 타이밍 전용이며, 독립적으로 거래 방향을 결정하거나 `trend_bias`를 뒤집어서는 절대 안 된다.

3. **내부_BOS를 통한 타이밍:** `internal_CHoCH` 발화 후, 스윙 추세 방향의 `internal_BOS`를 기다려 서브-레그 반전 완료와 주추세 재개를 확인한다. 이 `internal_BOS` 바가 1차 진입 트리거 바다.

4. **진입 회피:** 의도한 방향에 역행하는 `swing_CHoCH` 중에는 진입하지 않는다 — 구조가 거래 thesis를 무효화했다. `trend_bias`가 RANGING으로 전환될 때 보류 중인 진입을 일시 중지한다.

5. **EQH/EQL의 목표가 및 무효화 역할:** 현재 가격 위의 미티게이션 안 된(unmitigated) EQH는 BULLISH 구조에서 가장 가까운 상방 목표가; 미티게이션 안 된 EQL은 BEARISH 구조에서 하방 목표가. 가격이 EQL을 스윕(`liquidity_sweep` 이벤트)하지만 `trend_bias`가 BULLISH인 상태에서 EQL 위로 되돌아 종가를 형성하면, 이는 스윕-앤-리버설 패턴을 확인하며 다음 `internal_BOS`가 롱 진입을 발화한다.

6. **BOS를 통한 트레일링 스탑 갱신:** 추세 방향의 각 `swing_BOS`는 트레일링 스탑을 가장 최근 HL(BULLISH) 또는 LH(BEARISH)로 전진시켜야 하며, BOS 레벨 자체로 이동시켜서는 안 된다.

**컨플루언스** —

| 조건 | 방향 | 가중치 |
|------|------|--------|
| `trend_bias=BULLISH` + 최근 `swing_BOS` | 상승 | 0.30 |
| `trend_bias=BEARISH` + 최근 `swing_BOS` | 하락 | 0.30 |
| `internal_CHoCH` + `internal_BOS` (추세 방향) | 추세 방향 | 0.40 |
| `swing_CHoCH` (포지션 방향 역행) | **VETO** — 역방향 진입 가중치를 0으로 곱 | — |
| 고-타임프레임(HTF) 추세 정렬 | 추세 방향 | 0.35 |
| 모멘텀 오실레이터 | 참고용 | 0.15 |

시장구조는 가장 높은 레벨의 방향성 필터를 제공하며, 모멘텀 오실레이터(가중치 ~0.15)보다 위, 멀티-타임프레임 추세 정렬(가중치 ~0.35)보다 약간 아래에 위치해야 한다.

**거짓신호 가드** —

- **Wick-only 돌파 가드:** `bar.high > level`(bullish) 또는 `bar.low < level`(bearish)이지만 `bar.close`가 레벨을 돌파하지 않으면 BOS/CHoCH로 분류하지 않는다. 이 경우 `'liquidity_sweep'` 이벤트로 기록한다.
- **통합(RANGING) 가드:** `trend_bias == 'RANGING'`일 때 BOS/CHoCH 신호를 억제한다. RANGING 상태는 STEP 4의 결정론적 방법으로 판단: 최근 스윙 고점 2개 또는 저점 2개가 같은 방향 시퀀스가 아니면 bias는 RANGING.
- **최소 변위 가드:** `abs(b.close - level) / level < min_displacement_pct`인 BOS/CHoCH를 거부하여 다음 바에 자주 되돌아오는 경계선 종가를 필터링한다.
- **CHoCH 재발 가드:** 주어진 HL 또는 LH 피벗 레벨에서 CHoCH가 발화하면 해당 피벗을 `'consumed'`로 표시하고 동일 피벗에서 다시 CHoCH를 발화하지 않는다. 특정 피벗 가격의 최초 종가 돌파만 하나의 CHoCH 이벤트를 생성한다.
- **동일 피벗 중복 제거:** STEP 3에서 EQH 또는 EQL로 레이블된 피벗은 동일 레코드에 HH/LH/HL/LL 레이블을 같이 emit해서는 안 된다. 피벗 emission당 하나의 `event_type`.
- **BOS 레벨 전진 가드:** BOS 발화 후 즉시 BOS 트리거 포인터(`last_unbroken_swing_high`/`last_unbroken_swing_low`)를 다음 확정 미돌파 피벗으로 전진시킨다. 이 처리 없이는 동일 레벨에서 이후 매 바마다 재트리거되어 출력이 범람한다.
- **피벗 지연 인식:** 처리 바 인덱스 `i + swing_right`에서 확정된 스윙 피벗은 실제 고점/저점이 바 인덱스 i에 있음을 의미한다. 방금 확정된 피벗을 오른쪽의 아직 미확정 후보와 비교하지 않는다. 확정 바가 종가를 형성하기 전에 피벗을 노출시키지 않는다.
- **EQH/EQL 임계값 대칭성 가드:** 항상 `abs(p1 - p2) / max(p1, p2)`로 계산 — `p1`만으로 나누지 않는다 — 두 피벗이 어떤 순서로 도착해도 분류가 순서 독립적임을 보장.

**함정** —

- **CHoCH vs MSS 혼동:** ICT의 'Market Structure Shift'(MSS)는 반-추세 스윙 극단을 돌파할 때 변위 캔들(종종 FVG를 남기는)을 요구한다. 여기서 정의된 CHoCH는 변위를 요구하지 않으며 마지막 HL/LH의 캔들 종가 돌파만으로 발화한다. 이 둘을 혼용하면 중복 또는 모순 신호가 발생한다. MSS는 별도 FVG-displacement 탐지기에 속한다.
- **CHoCH 레벨 선택 변형:** 일부 SMC 교육자는 CHoCH 레벨을 가장 최근 확정 HL/LH가 아닌 직전 BOS 무브를 생성한 HL(또는 LH)로 지정한다. 이 변형은 CHoCH 이벤트 수는 적지만 확신도가 높다. 선택은 구현 시 고정되어야 한다. 현재 스펙은 가장 최근 확정 HL/LH를 사용(더 빈번, 덜 선택적).
- **추세 리셋 앵커 오류:** CHoCH 발화 후 `prev_sh_price`/`prev_sl_price`를 CHoCH 확인 바의 종가로 리셋하면 **오류**다. 새 추세의 구조적 원점은 가장 최근 확정 스윙 HIGH(새 하락 추세의 경우) 또는 스윙 LOW(새 상승 추세의 경우)이며, CHoCH 바의 임의 종가가 아니다.
- **이중 trend_bias 방법 충돌:** STEP 4는 롤링-투표 방법과 결정론적 2-피벗 방법 모두를 제공한다. 두 방법은 자주 괴리된다. 프로덕션 사용에서는 결정론적 2-피벗 규칙이 SMC에서 캐노니컬이며, 롤링-투표 방법은 진단 보조 용도로만 사용한다.
- **룩백 비대칭성:** `swing_left ≠ swing_right`는 유효하지만 확정 지연이 필요한 선행 패턴 창과 달라진다. 불균등 설정은 피벗이 소급 재레이블될 수 있으며 — 캔들이 후보 바 이후 `swing_right` 바가 종가를 형성한 후에만 피벗을 확정하는 엄격한 확정-전용(non-repainting) 로직으로 구현해야 한다.
- **내부 vs 스윙 스코프 오용:** `internal_CHoCH`와 `internal_BOS`는 스윙 구조 레그 내의 진입 타이밍 신호다. `trend_bias`를 절대 뒤집어서는 안 된다. 내부 피벗으로 전체 추세 방향을 결정하는 구현은 캐노니컬 접근보다 거짓 양성률이 2–3배 높다.
- **EQH/EQL 허용치 민감도:** 기본값 0.15%는 유동성 높은 대형주와 주요 외환 쌍에 잘 맞는다. 변동성 높은 알트코인이나 저유동성 상품에서는 0.05–0.08%로 좁히고; 저변동성 상품(예: 아시안 세션 외환)에서는 0.25–0.30%로 넓힌다.
- **교번 피벗 타입 가정:** 강한 추세에서는 중간 스윙 저점 없이 두 개의 연속 확정 스윙 고점이 나타날 수 있다. 알고리즘은 각 새 피벗을 인접 피벗이 아닌 가장 최근 동종 확정 피벗(`prev_sh_price`/`prev_sl_price`)과만 비교하므로 이를 올바르게 처리한다.
- **리페인팅 위험:** 알고리즘이 라이브 스트리밍 피드에서 실행될 경우, 후보 바 오른쪽에 `swing_right`개의 완전히 종가 형성 바가 존재할 때까지 피벗을 확정하지 않아야 한다. 그 이전의 레이블링은 리페인트되며 프로덕션 신호 생성이나 백테스팅에 부적합하다.
- **플랫-탑/플랫-바텀 피벗 공백:** STEP 1의 엄격히-큰 비교 규칙으로 인해 동일 고점의 시퀀스에서는 스윙 고점 피벗이 생성되지 않는다. 이는 의도적이고 캐노니컬하지만, 동일 상단을 가진 수평 통합 구간은 하나의 극단이 돌파될 때까지 구조적으로 침묵 상태로 보인다는 점을 소비자는 인식해야 한다.

**참고** —

- https://innercircletrader.net/tutorials/break-of-structure-vs-change-of-character/
- https://innercircletrader.net/tutorials/mss-vs-choch/
- https://innercircletrader.net/tutorials/ict-market-structure-shift/
- https://innercircletrader.net/tutorials/higher-high-and-higher-low/
- https://innercircletrader.net/tutorials/lower-high-and-lower-low/
- https://www.marketcalls.in/python/smart-money-concepts-smc-structures-and-fvg-a-python-tutorial.html
- https://docs.luxalgo.com/docs/algos/price-action-concepts/market-structures
- https://www.luxalgo.com/library/indicator/eqh-eql-liquidity-zones/
- https://fxfoundations.com/learn/technical-analysis/market-structure
- https://chartmini.com/blog/market-structure-trading-guide
- https://www.fluxcharts.com/articles/change-of-character-choch-explained
- https://traze.com/academy/advanced-strategies-forex-brokers/break-of-structure-vs-change-of-character/
- https://www.xs.com/en/blog/equal-highs-eqh/
- https://eplanetbrokers.com/en-US/training/break-of-structure-explained
- https://www.mindmathmoney.com/articles/understanding-break-of-structure-bos-and-change-of-character-choch-in-trading
- https://www.equiti.com/sc-en/news/trading-ideas/mss-vs-bos-the-ultimate-guide-to-mastering-market-structure/
- https://www.metatrader5.com/en/terminal/help/indicators/bw_indicators/fractals
- https://dailypriceaction.com/blog/smc-market-structure/
- https://www.writofinance.com/bos-vs-choch-in-forex/
- https://fxopen.com/blog/en/what-is-a-break-of-structure/
- https://www.luxalgo.com/blog/market-structure-shifts-mss-in-ict-trading/
- https://alchemymarkets.com/education/strategies/break-of-structure-bos-trading/
- https://www.mindmathmoney.com/articles/smart-money-market-structure-trading

---

## 2. 공정가치 갭 / 가격 불균형 (FVG — Fair Value Gap)

**정의** — 공정가치 갭(Fair Value Gap, FVG)은 시장이 한 방향으로 너무 급격하게 이동하여 첫 번째와 세 번째 캔들의 윅 범위가 겹치지 않고 미접촉 가격 구간이 남을 때 형성되는 3-캔들 가격 불균형이다. 상승 FVG에서 갭의 하단은 `candle[i-2].high`(zone_low), 상단은 `candle[i].low`(zone_high)이며, `c3.low`가 `c1.high`보다 엄격히 커야 한다. 하락 FVG에서 갭의 상단은 `candle[i-2].low`(zone_high), 하단은 `candle[i].high`(zone_low)이며, `c3.high`가 `c1.low`보다 엄격히 작아야 한다. 가운데 캔들(candle[i-1])은 변위(displacement) 캔들로, 방향이 갭 방향과 일치하는 과대-보디(oversized-body) 바다(상승 FVG에는 상승 보디, 하락 FVG에는 하락 보디). 미티게이션(mitigated) FVG는 가격이 진입 방향에서 갭 존에 재진입한 것이며, CE(Consequent Encroachment)는 갭의 정확한 50% 중간점이고, 완전 미티게이션(full mitigation)은 가격이 윅으로라도 갭의 반대편 끝(상승 FVG의 zone_low, 하락 FVG의 zone_high)에 도달함을 의미한다. 반전 FVG(Inverted FVG, IFVG)는 미티게이션된 FVG에서 가격이 확정된 캔들-보디 종가로 반대편 경계(상승 FVG의 zone_low, 하락 FVG의 zone_high)를 완전히 위반할 때 발생하며, 해당 존의 방향 역할이 반대 극성으로 영구 전환된다.

**탐지 알고리즘** —

1. **Step 1 — ATR 기준선:** 각 바 i에 대해 14기간(atr_period) 롤링 평균 진폭(ATR)을 계산한다. 바 j의 True Range: `TR[j] = max(bar[j].high - bar[j].low, abs(bar[j].high - bar[j-1].close), abs(bar[j].low - bar[j-1].close))`. `ATR14[i] = mean([i-13..i] TR)`. 경계 처리: `bar[0]`에는 이전 종가가 없으므로 `TR[0] = bar[0].high - bar[0].low`로 설정. 이 값은 동적 갭-크기 필터링에 사용된다.

2. **Step 2 — 평균 보디 기준선:** 각 바 i에 대해 `avg_body[i] = mean(abs(bar[j].close - bar[j].open) for j in range(i - body_lookback, i))`를 계산하며, `body_lookback=14`. 윈도우는 `[i-body_lookback .. i-1]`(바 i 자체 제외)로 하여 룩어헤드를 방지한다. 이 값은 변위 캔들 크기 검증에 사용된다.

3. **Step 3 — FVG 후보 스캔:** i를 2부터 `len(bars)-1`까지 순회한다. `c1=bars[i-2]`, `c2=bars[i-1]`, `c3=bars[i]`를 할당한다. 세 바 모두 유효한 OHLC를 가져야 한다(`c.high >= c.low`; `c.open`과 `c.close`가 모두 `[c.low, c.high]` 내에 있어야 함).

4. **Step 4 — 갭 체크 (상호 배타적 분기):** `bull_gap = c3.low - c1.high`와 `bear_gap = c1.low - c3.high`를 계산한다. 동시에 양수일 수 없으므로, 둘 다 `<= 0`이면 바 i를 건너뛴다. `bull_gap > 0`이면 상승 후보로 계속(Step 5a). `bear_gap > 0`이면 하락 후보로 계속(Step 5b). 바 하나가 동시에 둘 다일 수는 없다.

5. **Step 5a — 상승 최소 갭 크기 필터:** `gap_size = bull_gap`. `gap_size >= min_gap_atr_mult * ATR14[i]`를 요구한다. 충족하지 못하면 폐기.

6. **Step 5b — 하락 최소 갭 크기 필터:** `gap_size = bear_gap`. `gap_size >= min_gap_atr_mult * ATR14[i]`를 요구한다. 충족하지 못하면 폐기.

7. **Step 6 — 변위 캔들 방향 검증:** 상승 FVG에는 `c2.close > c2.open`(c2가 상승 캔들)을 요구한다. 하락 FVG에는 `c2.close < c2.open`(c2가 하락 캔들)을 요구한다. 가운데 캔들 보디 방향이 갭 방향과 모순되면 폐기한다. 이 검사는 보디-크기 필터 이전에 수행해야 한다.

8. **Step 7 — 변위 캔들 보디-크기 필터:** `c2_body = abs(c2.close - c2.open)`을 계산한다. `avg_body[i-1]`(바 i-2까지 계산되어 c2 이전 바로 끝남)을 사용하여 룩어헤드를 방지한다. `c2_body >= body_mult * avg_body[i-1]`을 요구한다. 충족하지 못하면 폐기.

9. **Step 8 — 강도 분류:** **STRONG**: `c3.close > c2.high`(상승) 또는 `c3.close < c2.low`(하락) — c3 바가 c2의 극단을 넘어 종가를 형성하여 기관 후속 추진을 확인. **WEAK**: 3-캔들 전체 형성이 이전 캔들의 범위 내에 포함되는 경우: `form_high = max(c1.high, c2.high, c3.high)`, `form_low = min(c1.low, c2.low, c3.low)`; `bars[i-2-swing_lookback : i-2]` 내의 각 이전 바 p에 대해 `p.high >= form_high AND p.low <= form_low`이면 WEAK으로 표시하고 break. **NORMAL**: STRONG도 WEAK도 아닌 경우.

10. **Step 9 — FVG 존 기록:** 상승: `zone_low = c1.high`, `zone_high = c3.low`, `zone_mid (CE) = (zone_low + zone_high) / 2`. 하락: `zone_high = c1.low`, `zone_low = c3.high`, `zone_mid (CE) = (zone_low + zone_high) / 2`. 불변량: `zone_low < zone_mid < zone_high` 항상 성립. `ts = c2.ts`(변위 캔들 타임스탬프), `direction = 'bullish'|'bearish'`, `strength = 'strong'|'normal'|'weak'`, `formation_bar_idx = i`, `gap_size = zone_high - zone_low`, `gap_size_atr = gap_size / ATR14[i]`, `mitigated=False`, `mitigation_type='none'`, `inverted=False`, `ifvg_active=False`를 기록.

11. **Step 10 — 미티게이션 추적:** 각 활성(`mitigated=False`) FVG에 대해 `formation_bar_idx` 이후의 모든 후속 바 j에서 다음 순서로 평가한다 — (a) **완전 미티게이션 먼저:** 상승 FVG: `bar[j].low <= zone_low`이면 → `mitigated=True`, `mitigation_type='full'`, `mitigation_ts=bar[j].ts`로 설정하고 Step 11의 반전 모니터로 진행 — 이 패스에서 CE도 동시에 설정하지 않는다. 하락 FVG: `bar[j].high >= zone_high`이면 → 동일하게 FULLY_MITIGATED. (b) **CE 체크 (완전 미티게이션이 아닌 경우에만):** 상승 FVG: `elif bar[j].low <= zone_mid` → `partial_mitigated_ce=True`, `mitigation_type='ce'`. 하락 FVG: `elif bar[j].high >= zone_mid` → `mitigation_type='ce'`. 이 순서는 하나의 격렬한 바가 동시에 CE와 완전-미티게이션 플래그를 설정하는 것을 방지한다.

12. **Step 11 — IFVG 반전 탐지:** 완전히 미티게이션된(`mitigated=True`) FVG만 모니터링 — 미티게이션 안 된 FVG는 반전될 수 없다. 미티게이션된 상승 FVG에 대해: `mitigation_ts` 이후 바 j에서 `bar[j].close < zone_low`(캔들 보디 종가가 하단 끝 아래로 엄격히 형성)이면 → `inverted=True`, `ifvg_active=True`, `ifvg_direction='bearish'`, `inversion_ts=bar[j].ts`로 표시. IFVG 존 경계는 변경되지 않음(`zone_low`, `zone_high`, `zone_mid` 동일) — 동일 가격 레벨이 이제 하락 저항으로 작용. 미티게이션된 하락 FVG에 대해: `bar[j].close > zone_high`이면 → `inverted=True`, `ifvg_active=True`, `ifvg_direction='bullish'`, `inversion_ts=bar[j].ts`. 보디 종가만 인정; 윅만의 침투는 반전을 트리거하지 않음(캐노니컬 ICT 규칙). `ifvg_require_body_close=False`이면 `bar[j].low < zone_low`(상승) 또는 `bar[j].high > zone_high`(하락)만으로도 충분.

13. **Step 12 — IFVG 미티게이션 및 무효화:** 활성 상승 IFVG(전 하락 FVG, `ifvg_active=True`)에 대해: `bar[j].close < ifvg_zone_low`(= `zone_low`)이면 → `ifvg_active=False`(이제 상승 존의 하단 경계 미만 종가로 무효화). 활성 하락 IFVG(전 상승 FVG)에 대해: `bar[j].close > ifvg_zone_high`(= `zone_high`)이면 → `ifvg_active=False`.

14. **Step 13 — 출력:** FVGRecord 객체 리스트를 `ts` 기준 시간 순으로 정렬하여 반환한다. 별도 리스트 유지: `active_fvgs`(`mitigated=False, inverted=False`), `mitigated_fvgs`(`mitigated=True`), `active_ifvgs`(`ifvg_active=True`). 실시간 사용 시 `bar[j].ts <= current_timestamp`인 바만 처리(룩어헤드 없음). `formation_bar_idx=i`의 바는 패턴을 완성하는 첫 번째 바이며 탐지 시점에 종가가 이미 알려져 있다 — 미래 바 데이터가 필요하지 않다.

**파라미터** —

| name | default | 의미 |
|------|---------|------|
| `min_gap_atr_mult` | `0.15` | ATR14의 배수로 표현된 최소 FVG 갭 크기. 이보다 작은 갭은 유의미하지 않은 노이즈로 폐기. 범위 0.10–0.50; 0.15는 커뮤니티 파생 실용 하한값. |
| `body_mult` | `1.15` | 변위 캔들(c2) 보디가 `avg_body * body_mult`를 초과해야 함. 가운데 캔들이 진정한 기관 모멘텀을 나타내도록 보장. 범위 1.0–2.0. |
| `body_lookback` | `14` | 변위 캔들 필터를 위한 롤링 평균 보디 크기 계산에 사용되는 바 수(c2 이전 한 바에서 끝남). c2 이전에 끝내어 룩어헤드 편향을 방지. |
| `atr_period` | `14` | 갭 크기 필터링에 사용하는 ATR 계산 기간. |
| `swing_lookback` | `5` | FVG를 WEAK으로 분류할 때 체크할 바 수(3-캔들 형성이 이전 캔들의 윅 범위 내에 완전히 포함되는지 확인하기 위해 c1 이전 엄격한 바들). |
| `ifvg_require_body_close` | `True` | True(캐노니컬 ICT 규칙)이면 IFVG 반전은 FVG 반대편 끝(상승의 zone_low, 하락의 zone_high)을 넘는 캔들-보디 종가로만 트리거. False이면 윅 침투만으로도 충분(비캐노니컬, 거짓 양성률 높음). |
| `max_active_fvgs` | `50` | 심볼/타임프레임 쌍당 동시 추적 가능한 최대 활성(미티게이션 안 됨) FVG 수. 한계에 도달하면 가장 오래된 것을 퇴출. |

**출력 필드** —

- `fvg_id: str` — 고유 식별자, 예: `'{symbol}_{freq}_{ts}_{direction}'`
- `symbol: str`
- `freq: str` — 예: `'4h'`, `'1d'`
- `direction: str` — `'bullish'` | `'bearish'`
- `ts: datetime` — 변위 캔들(c2)의 타임스탬프; 인트라데이 타임프레임 처리를 위해 bare date가 아닌 datetime 사용
- `formation_bar_idx: int` — 패턴을 완성하는 c3 바의 입력 리스트 내 정수 인덱스
- `zone_low: float` — FVG 존의 하단 경계(상승은 `= c1.high`; 하락은 `= c3.high`)
- `zone_high: float` — FVG 존의 상단 경계(상승은 `= c3.low`; 하락은 `= c1.low`)
- `zone_mid: float` — CE(Consequent Encroachment), 정확한 중간점 `= (zone_low + zone_high) / 2`; 불변량: `zone_low < zone_mid < zone_high`
- `gap_size: float` — `zone_high - zone_low`(절대 가격 거리)
- `gap_size_atr: float` — 형성 바에서의 `gap_size / ATR14`
- `strength: str` — `'strong'` | `'normal'` | `'weak'`
- `mitigated: bool` — 어떤 윅이라도 갭에 진입했을 때 True(CE 또는 완전)
- `partial_mitigated_ce: bool` — 바의 윅이 `zone_mid`에 도달했지만 `zone_low`/`zone_high`에는 미치지 않은 경우(CE 터치, 완전 미티게이션 아님) 특정적으로 True
- `mitigation_type: str` — `'none'` | `'ce'` | `'full'`
- `mitigation_ts: datetime | None`
- `inverted: bool` — 완전 미티게이션된 FVG가 이후 반대편 경계를 넘는 보디 종가로 위반된 경우 True
- `ifvg_active: bool` — 결과 IFVG가 아직 유효(아직 무효화되지 않음)하면 True
- `ifvg_direction: str | None` — `'bullish'` | `'bearish'`(항상 원래 방향의 반대)
- `inversion_ts: datetime | None`

**진입 관련성** —

FVG는 대기(WAIT) 상태를 신호한다: 가격이 존에서 멀리 있는 동안은 진입하지 않는다. 진입 트리거는 가격이 존으로 되돌아올 때 발화한다.

1. **진입 존:** 상승 FVG에서 실행 가능한 진입 범위는 `zone_low`부터 `zone_high`까지이며, 가장 높은 확률의 서브-존은 `zone_low`부터 `zone_mid`(CE)까지로 기관 지정가 주문이 집중된다. 하락 FVG에서는 `zone_mid`부터 `zone_high`까지가 가장 높은 확률의 서브-존이다.

2. **진입 트리거:** FVG 타임프레임보다 한 단계 낮은 타임프레임에서, 실행 전에 FVG 존 내부의 하위 타임프레임 시장구조 전환(CHoCH 또는 BoS)을 기다린다. 이는 가격이 반응 없이 존을 단순히 통과하는 것을 피하게 한다.

3. **회피 상태:** 다음의 경우 진입하지 않는다: (a) FVG `mitigation_type`이 `'ce'` 또는 `'full'`(존이 이미 손상됨); (b) FVG 방향이 HTF 편향에 역행; (c) `strength`가 `'weak'`; (d) 가격이 잘못된 방향에서 존에 접근 — 예를 들어 상승 FVG에서 가격이 이미 `zone_low` 아래에 있음(완전 미티게이션이 임박했거나 이미 발생했음을 의미); (e) 바가 FVG가 형성된 것과 같은 바인 경우(c3 종가) — 재테스트가 아직 발생하지 않았으므로 존은 최소 한 바 이상 지속되어야 한다.

4. **IFVG 진입:** `ifvg_active=True`이면 새 방향(`ifvg_direction`)으로 IFVG 존의 재테스트 시 진입한다. 손절매는 IFVG 반대편 경계에서 1 ATR 너머에 설정(상승 IFVG는 `zone_low` 아래, 하락 IFVG는 `zone_high` 위).

**컨플루언스** —

| 조건 | 방향 | 가중치 |
|------|------|--------|
| FVG `strength='strong'` + HTF 오더블록 또는 유동성 스윕과 일치 | FVG 방향 | 0.65 |
| NORMAL strength 독립 FVG | FVG 방향 | 0.45 |
| WEAK strength FVG | FVG 방향 | 0.25 |
| 선행 유동성 스윕 + 시장구조 전환(CHoCH/BoS) 동반 | FVG 방향 | +보너스 |
| 디스카운트 존(상승) 또는 프리미엄 존(하락) 배치 | FVG 방향 | 포함 조건 |
| 반응 없이 zone 통과 이력(HTF 정렬 없음) | — | 0.20으로 감소 |

FVG 신호는 다음과 결합될 때 가장 강하다: (a) 스윙 저점/고점의 선행 유동성 스윕, (b) 동일 또는 하위 타임프레임 시장구조 전환(CHoCH/BoS), (c) 현재 스윙 범위의 50% 아래(롱) 또는 위(숏)에 위치한 FVG. `gap_size_atr`이 3.0을 초과하면(잠재적 돌파 갭) 평균 회귀 확률이 낮으므로 가중치를 0.20으로 감소시킨다.

**거짓신호 가드** —

- `gap_size >= min_gap_atr_mult * ATR14`를 요구하여 저변동성 횡보에서 형성된 마이크로-갭을 제거 — 이 단일 필터가 원시 탐지의 60–80%를 제거한다.
- 변위 캔들(c2) 보디가 `avg_body * body_mult`를 초과하도록 요구 — 진정한 기관 변위가 아닌 느린 그라인딩 가격 움직임으로 생성된 FVG를 거부.
- c2 방향이 갭과 일치하도록 요구: 상승 FVG는 `c2.close > c2.open`, 하락 FVG는 `c2.close < c2.open` — 혼합 신호 갭 방지(이 체크는 보디-크기 필터 이전에 수행).
- WEAK 강도 FVG는 거래하지 않는다(3-캔들 형성이 이전 대형 캔들의 윅 범위 내에 포함됨 — 구조적 돌파 변위가 없음).
- 동일 타임프레임에서 이미 완전 미티게이션된 존과 완전히 겹치는 FVG를 폐기 — 해당 레벨의 기관 주문이 소진됨.
- IFVG에서: 반전을 위해 확정된 캔들-보디 종가가 반대편 경계를 넘도록 요구(`ifvg_require_body_close=True`) — 윅만의 침투는 반전에 대해 높은 거짓 양성률을 가짐.
- 프리미엄/디스카운트 컨텍스트 확인: 상승 FVG는 가격이 현재 스윙 범위의 디스카운트 절반(50% 아래)에 있을 때만 거래; 하락 FVG는 프리미엄 절반에서만.
- 진입 전 최소 한 바의 존재가 완료되어야 한다 — c3(형성 바) 자체에서는 진입하지 않는다(아직 재테스트가 발생하지 않음).
- Step 10 상태머신 정확성: 동일 바 내에서 완전 미티게이션 체크를 CE 체크보다 먼저 적용하고, `mitigated=True`로 설정되면 CE 분기를 완전히 건너뛴다.

**함정** —

- **윅 vs 보디 경계:** 캐노니컬 ICT 규칙은 윅 극단(`c1.high`와 `c3.low` for 상승)을 사용하며, 캔들 보디 경계가 아니다. 보디 엣지를 잘못 사용하는 일부 커뮤니티 구현은 더 작고 신뢰성 낮은 존을 생성한다. 이 스펙은 올바르게 윅 경계를 사용한다.
- **Step 10 상태머신 순서:** `bar[j].low <= zone_low`(완전 미티게이션)가 TRUE이면 `bar[j].low <= zone_mid`도 TRUE다(`zone_low < zone_mid`이므로). 명시적 else-if 순서 없이는 하나의 격렬한 바가 동시에 `'ce'`와 `'full'`을 설정하여 `mitigation_type`을 손상시킨다. 항상 완전 미티게이션을 먼저 평가하고 이후 CE 분기를 건너뛴다.
- **IFVG는 이전 완전 미티게이션을 전제조건으로 요구:** Step 11의 스펙은 완전히 미티게이션된 FVG만 모니터링하는데, 이는 캐노니컬 ICT/LuxAlgo 정의에 맞는 올바른 방법이다 — 미티게이션 안 된 FVG는 반전될 수 없다. 미티게이션 안 된 FVG를 반전에 대해 모니터링하지 않는다.
- **IFVG 트리거 vs 동일 바 모호성:** 완전 미티게이션을 트리거하는 보디 종가(`bar[j].close < zone_low` for 상승 FVG)가 한 바의 처리 내에서 완전 미티게이션 조건과 반전 트리거를 동시에 충족할 수 있다. 완전 미티게이션을 먼저 처리하고 반전 조건도 충족된다면 동일 바에서 `inverted`를 표시한다.
- **avg_body 룩어헤드:** `avg_body[i-1]`을 `[i-body_lookback : i]` 범위(바 i-1 포함, 바 i 미포함)로 계산하는 것은 안전하지만, `[i-body_lookback+1 : i]` 또는 `[i-body_lookback : i+1]`로 계산하면 룩어헤드가 발생한다. 보디 윈도우를 항상 바 i-2(c2 이전 한 바)에서 끝내는 것이 엄격하게 안전하다.
- **미티게이션 정의의 소스별 차이:** 일부는 미티게이션을 존에 대한 윅 진입으로 정의하고, 다른 이는 존 내 보디 종가를 요구한다. 이 스펙은 CE에는 윅-진입(`bar[j].low <= zone_mid`), 완전 미티게이션에도 윅-진입(`bar[j].low <= zone_low`)을 사용하며, 이는 가장 널리 인용되는 ICT 커뮤니티 표준과 일치한다. 보디-종가-전용 미티게이션은 더 엄격한 비기본 변형이다.
- **타임프레임 의존성:** 상위 타임프레임(일봉, 4시간봉)의 FVG는 하위 타임프레임 FVG보다 훨씬 큰 비중을 가진다. 4시간봉 FVG 내부의 1분봉 FVG는 종속적이며 — 중요도를 혼용하지 않는다.
- **뉴스 드리븐 메가-갭:** `gap_size_atr > 3.0`은 일반적으로 저 평균 회귀 확률의 매크로 이벤트 돌파 갭(FOMC, 실적)을 나타낸다. 별도로 플래그하거나 ATR 상한을 고려한다.
- **BPR(Balanced Price Range) 혼동:** 상승 FVG와 하락 FVG가 동일 가격 영역에서 겹칠 때, 겹치는 영역(BPR)이 가장 높은 확률의 반응 존이다 — 어느 FVG의 전체 범위도 아니다. 반대 방향 FVG 겹침을 스캔하여 BPR을 식별한다.
- **swing_lookback 앵커 모호성:** '마지막 swing_lookback 바'는 `bars[i-2-swing_lookback : i-2]`(c1 이전 엄격한 바들)로 앵커되어야 하며 형성 캔들 자체를 포함하지 않아야 한다.

**참고** —

- https://innercircletrader.net/tutorials/fair-value-gap-trading-strategy/
- https://innercircletrader.net/tutorials/valid-ict-fair-value-gap/
- https://innercircletrader.net/tutorials/ict-inversion-fair-value-gap/
- https://innercircletrader.net/tutorials/ict-consequent-encroachment/
- https://www.fluxcharts.com/articles/Trading-Concepts/Price-Action/Fair-Value-Gaps
- https://www.fluxcharts.com/articles/inversion-fair-value-gaps-ifvg-explained
- https://dailypriceaction.com/blog/fair-value-gap/
- https://eplanetbrokers.com/training/what-is-fair-value-gap
- https://medium.com/@ziad.francis/automating-fair-value-gaps-fvg-in-python-0768d3f382e6
- https://github.com/joshyattridge/smart-money-concepts
- https://ftmo.com/en/blog/catch-the-reversal-trading-the-inverse-fair-value-gap-ifvg-strategy/
- https://www.luxalgo.com/library/indicator/Inversion-Fair-Value-Gaps-IFVG/
- https://www.writofinance.com/consequent-encroachment-and-mean-threshold/
- https://trendspider.com/learning-center/fair-value-gap-trading-strategy/
- https://thesimpleict.com/fair-value-gaps-fvg-imbalance-ict/

---

## 3. 오더블록 (order_block)

**정의** — ICT/SMC 오더블록(Order Block, OB)은 임박한 임펄스 방향 이동이 시작되기 직전의 마지막 역방향 캔들이다 — 상승 임펄스 직전의 마지막 하락 캔들이 Bullish OB이고, 하락 임펄스 직전의 마지막 상승 캔들이 Bearish OB다. OB 존은 기관 지정가 주문이 체결된 가격 영역을 나타내며, 정제(refined) 모드에서는 해당 캔들의 보디(open~close)로, 미정제(unrefined) 모드에서는 전체 캔들 범위(high~low)로 정의된다. 유효한 OB의 조건: (1) 변위 임펄스 캔들 보디가 임펄스 방향으로 OB 캔들의 전체 윅(wick) 극단을 종가로 돌파해야 하며(`BOS_UP`이면 OB 캔들의 윅 고점 상방 종가; `BOS_DOWN`이면 OB 캔들의 윅 저점 하방 종가), (2) 임펄스가 캔들 보디 종가 돌파로 이전 스윙 고점 또는 저점을 돌파해야 하며 — 윅만의 침투는 구조적 돌파(BOS/CHoCH)가 아님, (3) OB는 가격이 OB 캔들의 전체 윅 원점 극단을 종가로 돌파하지 않는 한 활성(미티게이션 안 됨) 상태를 유지한다(bullish OB는 OB 캔들의 전체 저점 미만 종가; bearish OB는 OB 캔들의 전체 고점 초과 종가) — 미티게이션 극단은 `use_body_only` 설정과 무관하게 항상 전체 캔들 윅을 사용한다. Bullish OB가 전체 저점 미만 종가로 위반되거나 Bearish OB가 전체 고점 초과 종가로 위반될 때, 그 위반 이전에 이전 스윙의 유동성 스윕이 선행됐다면 이는 Breaker Block에 해당한다 — 이제 역방향에서 재테스트 시 저항(전 bullish OB breaker) 또는 지지(전 bearish OB breaker)로 작용한다; 유동성 스윕 없이 위반된 것은 저확률 실패 OB(mitigation block)이며 캐노니컬 Breaker가 아니다.

**탐지 알고리즘** —

1. **STEP 1 — 롤링 ATR(14) 및 정확한 True Range 계산:** 각 바 i(i >= 1)에 대해 먼저 True Range를 계산한다: `TR[i] = max(bar[i].high - bar[i].low, abs(bar[i].high - bar[i-1].close), abs(bar[i].low - bar[i-1].close))`. `i=0`에서는 `TR[0] = bar[0].high - bar[0].low`(이전 종가 없음). Wilder's Smoothing(RMA/SMMA, factor `alpha=1/atr_period`)을 사용하여 ATR을 계산: `atr14[atr_period-1] = mean(TR[0..atr_period-1])`로 초기화; `i >= atr_period`에서: `atr14[i] = atr14[i-1] * (1 - 1/atr_period) + TR[i] * (1/atr_period)`. `statistics.mean(bar.high - bar.low)`를 사용하지 말 것 — 이 공식은 갭을 무시하고 실제 변동성을 과소평가한다. ATR은 STEP 4의 변위 임계값으로 사용된다.

2. **STEP 2 — 스윙 포인트 탐지 (라이브-안전 지연 포함):** 각 바 i에서 `i >= swing_lookback AND i <= len(bars) - swing_lookback - 1`인 경우(참고: 오른쪽 `swing_lookback` 바는 아직 확정될 수 없음 — 이는 `swing_lookback` 바의 확정 스윙 지연을 생성하며, 룩어헤드 편향 방지를 위해 라이브/스트리밍 사용에서 반드시 준수해야 한다): `is_swing_high[i] = (bar[i].high == max(bar[j].high for j in range(i - swing_lookback, i + swing_lookback + 1)))`; `is_swing_low[i] = (bar[i].low == min(bar[j].low for j in range(i - swing_lookback, i + swing_lookback + 1)))`. `swing_highs = [(i, bar[i].high) for i with is_swing_high]`와 `swing_lows = [(i, bar[i].low) for i with is_swing_low]`를 인덱스 오름차순으로 정렬하여 구성. 라이브 모드에서는 `bar[i + swing_lookback]`이 종가를 형성한 후에만 스윙 확정을 emit한다.

3. **STEP 3 — 구조 돌파(BOS/CHoCH):** 가장 최근 돌파된 스윙 고점 포인터(`BOS_UP` 추적용)와 스윙 저점 포인터(`BOS_DOWN` 추적용)를 유지한다. 첫 번째 확정 바부터 시간 순으로 각 바 k를 처리하며: **BOS_UP**(바 k): `bar[k].close > most_recent_unbroken_swing_high.price`일 때 발생하며, `most_recent_unbroken_swing_high`는 인덱스 < k이고 이전 `BOS_UP` 이벤트에 이미 돌파되지 않은 `swing_highs` 리스트 내 가장 높은 스윙 고점 항목이다. **BOS_DOWN**(바 k): `bar[k].close < most_recent_unbroken_swing_low.price`일 때 발생하며, 유사 조건. `bos_events`를 `(k, direction, broken_swing_index, broken_swing_price)` 리스트로 기록. **보디 종가만 유효** — 보디 종가 없이 스윙 고점 위 윅만 돌파하는 것은 유동성 스윕/스탑 헌트이며 BOS가 아니다; OB를 등록하지 않는다.

4. **STEP 4 — 변위 캔들 필터:** 바 k에서의 각 BOS 이벤트에 대해: 변위 캔들은 `bar[k]` 자체다. 계산: `body_size = abs(bar[k].close - bar[k].open)`; `full_range = bar[k].high - bar[k].low`. 검증: (a) `body_size >= displacement_atr_mult * atr14[k]`(변위 보디가 최소 `displacement_atr_mult` ATR 이상이어야 함); (b) `body_to_range_ratio = body_size / (full_range + 1e-12) >= body_ratio_min`(과도한 윅이 있는 도지형 캔들 필터). `BOS_UP`에서: `bar[k].close > bar[k].open`(상승 변위 캔들)이어야 함. `BOS_DOWN`에서: `bar[k].close < bar[k].open`(하락 변위 캔들)이어야 함. 어느 검증이라도 실패하면 해당 BOS는 유효한 OB를 생성하지 않으므로 건너뛴다.

5. **STEP 5 — 마지막 역방향 캔들 식별 (OB 캔들):** `BOS_UP`(바 k)에서: `bar k-1`부터 `max(k - ob_lookback_bars, 0)` 방향으로 역방향 스캔. `ob_i = [max(k - ob_lookback_bars, 0), k-1]` 범위 내에서 `bar[j].close < bar[j].open`(마지막 하락 캔들)을 만족하는 최대 인덱스 j. `BOS_DOWN`(바 k)에서: `ob_i = [max(k - ob_lookback_bars, 0), k-1]` 범위 내에서 `bar[j].close > bar[j].open`(마지막 상승 캔들)을 만족하는 최대 인덱스 j. '마지막'이란 변위 바 직전의 가장 최근(최대 인덱스) 적격 캔들을 의미한다. `ob_lookback_bars` 내에 역방향 캔들이 없으면 이 BOS 이벤트를 건너뛴다.

6. **STEP 6 — 엔걸프먼트 체크 (전체 캔들 극단 사용):** `BOS_UP`(ob_i에서 Bullish OB): 변위 캔들 보디가 OB 캔들의 **전체 HIGH(윅 포함)** 위로 종가를 형성해야 함: `bar[k].close > bar[ob_i].high`. `BOS_DOWN`(ob_i에서 Bearish OB): `bar[k].close < bar[ob_i].low`(OB 캔들의 전체 저점/윅 저점 미만 보디 종가). 이것이 캐노니컬 ICT 엔걸프먼트로 — 변위가 임펄스 방향으로 OB 캔들의 극단을 종가로 초과하여 이전 캔들의 전체 범위가 소비됐음을 보장한다. 엔걸프먼트 실패 시 건너뛴다.

7. **STEP 7 — OB 존 경계 계산:** 정제 모드(`use_body_only=True`): `bull_ob_top = max(bar[ob_i].open, bar[ob_i].close)`; `bull_ob_bottom = min(bar[ob_i].open, bar[ob_i].close)`. `bear_ob_top = max(bar[ob_i].open, bar[ob_i].close)`; `bear_ob_bottom = min(bar[ob_i].open, bar[ob_i].close)`. 미정제 모드(`use_body_only=False`): `ob_top = bar[ob_i].high`; `ob_bottom = bar[ob_i].low`. `Midpoint = (ob_top + ob_bottom) / 2`. **참고:** 존 경계는 **진입 목표 존**만 정의한다. **미티게이션 경계**(무효화 기준)는 `use_body_only`와 무관하게 항상 전체 캔들 극단을 사용: `mitigation_extreme_bull = bar[ob_i].low`(전체 윅 저점); `mitigation_extreme_bear = bar[ob_i].high`(전체 윅 고점). 이 두 값은 `zone_high`/`zone_low`와 별도로 저장되어야 한다.

8. **STEP 8 — 선택적 Fair Value Gap 확인:** OB 캔들(`ob_i`)과 변위 캔들(`k`) 사이에서 3-캔들 FVG를 확인한다. `ob_i <= a < b < c <= k`인 연속 삼중(`a, b, c`)에 대해: `bar[c].low > bar[a].high`이면 bullish FVG 존재(캔들 1 전체 고점과 캔들 3 전체 저점 사이의 갭 — 변위 캔들 b가 급등하여 불균형 남김); `bar[c].high < bar[a].low`이면 bearish FVG. 갭은 0이 아니어야 한다(`bar[c].low`가 `bar[a].high`보다 엄격히 커야 하며 윅 포함 겹침 없음). `ob_i`와 `k` 사이에서 비-겹침 갭이 발견되면 `has_fvg = True` 기록.

9. **STEP 9 — OB 강도 점수 계산 (0.0–1.0):** `score = 0.0`; 다음 조건 각각 충족 시 가산: `+0.30` if `body_size >= displacement_atr_mult * 2.0 * atr14[k]`(임계값 2배의 강한 변위); `+0.25` if `has_fvg`(FVG가 OB와 변위 사이 불균형 확인); `+0.20` if `bar[ob_i].volume > mean(bar[j].volume for j in range(max(0, ob_i-14), ob_i))`(OB 캔들의 평균 이상 거래량이 기관 주문 체결 내러티브를 지지); `+0.15` if `body_to_range_ratio >= 0.65`(최소 윅의 깔끔한 변위); `+0.10` if `is_swing_low[ob_i] or is_swing_high[ob_i]`(OB 캔들 자체가 확정된 스윙 포인트임). 최대 합계 = 1.00.

10. **STEP 10 — 활성 OB 등록:** `active_obs` 리스트에 추가: `dict(ob_index=ob_i, direction='bullish' if BOS_UP else 'bearish', zone_high=ob_top, zone_low=ob_bottom, zone_mid=(ob_top+ob_bottom)/2, mitigation_extreme=mitigation_extreme_bull if BOS_UP else mitigation_extreme_bear, ts=bar[ob_i].ts, bos_ts=bar[k].ts, strength=score, mitigated=False, visited=False, is_breaker=False, has_fvg=has_fvg, liquidity_swept=False, htf_confluence=False, oi_confirmation=False, mitigation_ts=None, breaker_direction=None, breaker_retest_ts=None)`.

11. **STEP 11 — 미티게이션 및 방문 추적 (k 이후 모든 바 p를 시간 순으로 스캔):** 각 활성 Bullish OB에 대해: (a) **VISITED:** `not ob.visited and bar[p].low <= ob.zone_high`이면 `ob.visited = True` 설정(존 상단 최초 터치 — 미티게이션 전 진입 창). (b) **MITIGATION:** `close_mitigation=True`이고 `bar[p].close < ob.mitigation_extreme`(OB 캔들의 **전체 LOW** 미만 종가, `zone_low` 아님)이면 `ob.mitigated = True`와 `ob.mitigation_ts = bar[p].ts` 설정. `close_mitigation=False`이면 `bar[p].low < ob.mitigation_extreme`에서 미티게이션 트리거. (c) **BREAKER 체크:** breaker로 전환하려면 추가적으로 `ob.liquidity_swept = True`가 필요(STEP 11b 참조). `mitigated=True AND liquidity_swept=True`인 경우에만: `ob.is_breaker = True`와 `ob.breaker_direction = 'bearish_breaker'` 설정. 활성 Bearish OB에 대해: (a) `bar[p].high >= ob.zone_low`이면 `visited=True`. (b) `bar[p].close > ob.mitigation_extreme`(OB 캔들 전체 HIGH 초과 종가)이면 `mitigated=True`. (c) 동일한 `liquidity_swept` 요구; 충족 시 `breaker_direction = 'bullish_breaker'`. **중요:** `close_mitigation=True`일 때 `mitigation_extreme` 이상/이하의 윅 전용 터치는 미티게이션을 트리거하지 않는다.

12. **STEP 11b — 유동성 스윕 탐지 (Breaker Block 지정의 전제조건):** Bullish OB에 대한 유동성 스윕은 미티게이션 이벤트 이전에 어떤 바 p'(`k < p' <= p`)가 `bar[p'].low < most_recent_swing_low_before_k.price`(가격이 `BOS_UP` 이벤트를 앵커링한 이전 스윙 저점 미만으로 스윕)를 만족하고 이후 미티게이션이 트리거될 때 발생한다. Bearish OB에 대한 유동성 스윕: `bar[p'].high > most_recent_swing_high_before_k.price`. 미티게이션 바 이전에 스윕이 발생하지 않았다면 `ob.liquidity_swept = False`로 설정; 실패한 OB는 mitigation block(저확률)이지만 캐노니컬 Breaker Block이 **아니다**. 스윕 조건이 충족될 때만 `ob.liquidity_swept = True` 기록.

13. **STEP 12 — Breaker Block 재테스트:** `is_breaker=True`이고 `breaker_retest_ts is None`인 OB에 대해: `ob.mitigation_ts` 이후 바 `p_r`에서, Bearish Breaker(전 bullish OB)는 `bar[p_r].high >= ob.zone_low`일 때(가격이 아래에서 존 안으로 상승) 재테스트되고 있음; 첫 발생 시 `breaker_retest_ts = bar[p_r].ts` 설정. Bullish Breaker(전 bearish OB)는 `bar[p_r].low <= ob.zone_high`일 때 재테스트. 최초 재진입만 기록.

14. **STEP 13 — 멀티-타임프레임 컨텍스트 (선택적):** 하위 타임프레임에서 탐지된 각 OB에 대해, 상위 타임프레임의 활성(`mitigated=False`) OB와 `ob.zone_low <= htf_ob.zone_high AND ob.zone_high >= htf_ob.zone_low` 여부를 확인한다. 겹침이 있으면 `htf_confluence=True` 설정. 강도 점수에 +0.15 가산(1.0 상한 적용).

15. **STEP 14 — 오픈 인터레스트 강화 (크립토/선물, 선택적):** 변위 바 k에서 `oi[k] > oi[k-1]`이고 방향이 OB bias와 일치하면(`BOS_UP`에 대한 bullish OB에서 상승 OI, 또는 `BOS_DOWN`에 대한 bearish OB에서 상승 OI), `oi_confirmation=True` 설정. OB 존 재테스트 시 OI 상승도 추가 컨플루언스를 더한다.

16. **STEP 15 — 출력:** `active_obs`를 `strength >= min_strength_score`로 필터링(`require_fvg=True`이면 `has_fvg` 추가 요구). 탐지된 각 OB/Breaker에 대해 모든 출력 필드가 채워진 딕셔너리 리스트를 반환한다.

**파라미터** —

| name | default | 의미 |
|------|---------|------|
| `swing_lookback` | `2` | 스윙 고점 또는 저점으로 자격을 부여하기 위해 바 양쪽에 필요한 바 수(피벗 탐지 반경). `swing_lookback=2`는 중심 바를 포함한 5바의 최고점이어야 스윙 고점. 라이브 모드에서 바 i의 스윙은 `bar[i + swing_lookback]`이 종가를 형성한 후에만 확정 — `swing_lookback` 바의 확정 지연 발생. |
| `ob_lookback_bars` | `10` | BOS 변위 캔들에서 마지막 역방향 캔들을 탐색할 때 되돌아볼 최대 바 수. 값이 클수록 더 넓은 통합 범위의 OB를 포착하지만 노이즈도 증가. |
| `displacement_atr_mult` | `1.0` | 변위 캔들 보디의 최소 크기를 ATR(14, Wilder) 배수로 표현. 보디가 `displacement_atr_mult * ATR(14)` 미만이면 해당 BOS는 OB를 생성하지 않음. 1.0은 변위 보디가 최소 1 ATR이어야 함을 의미. 크립토 인트라데이에서는 0.7로 낮추거나 일봉 외환에서는 1.5로 올리는 것을 고려. |
| `body_ratio_min` | `0.50` | 변위 캔들 보디(`\|close-open\|`)의 전체 캔들 범위(high-low) 대비 최소 비율. 과도한 윅이 있는 우유부단한 캔들을 필터링. 0.50은 보디가 전체 범위의 최소 50%를 차지해야 함. |
| `use_body_only` | `True` | True(정제 OB)이면 진입 목표 존이 캔들 보디(open~close)로 정의됨. False(미정제)이면 존이 전체 범위(high~low)를 사용. 미티게이션 경계에는 영향을 주지 않으며, 미티게이션 경계는 항상 전체 윅 극단을 사용(bullish OB는 candle.low, bearish OB는 candle.high). |
| `close_mitigation` | `True` | True이면 이후 캔들이 OB 캔들의 전체 윅 원점 극단을 **종가로** 돌파해야만 OB가 미티게이션됨(bullish OB: `candle.low` 미만 종가). False이면 극단의 윅 터치만으로 미티게이션. ICT 캐노니컬: `close_mitigation=True`. |
| `min_strength_score` | `0.40` | 출력에 포함되기 위한 최소 강도 점수(0.0~1.0). 이 임계값 미만의 OB는 저품질로 필터링. |
| `require_fvg` | `False` | True이면 OB 캔들과 변위 캔들 사이에 비-겹침 Fair Value Gap(bullish는 캔들 3 저점이 캔들 1 고점보다 엄격히 높음; bearish는 캔들 3 고점이 캔들 1 저점보다 엄격히 낮음)이 존재하는 OB만 출력. 고확률 ICT 셋업에 맞는 더 엄격한 필터. |
| `atr_period` | `14` | Wilder's Smoothing(`alpha = 1/atr_period`)을 사용하는 ATR 계산 룩백 기간. Wilder 권장 기간. 변위 필터링에 사용. 단순이동평균이 아님 — 전체 True Range = `max(H-L, \|H-prev_C\|, \|L-prev_C\|)`를 사용. |

**출력 필드** —

- `ob_index: int` — 입력 리스트 내 OB 캔들의 바 인덱스(0-기반)
- `direction: str` — `'bullish'`(상방 변위 직전 마지막 하락 캔들) 또는 `'bearish'`(하방 변위 직전 마지막 상승 캔들)
- `zone_high: float` — 진입 목표 OB 존의 상단 경계(`use_body_only`이면 보디 상단, 그 외 캔들 윅 고점)
- `zone_low: float` — 진입 목표 OB 존의 하단 경계(`use_body_only`이면 보디 하단, 그 외 캔들 윅 저점)
- `zone_mid: float` — 진입 존의 중간점 = `(zone_high + zone_low) / 2`; 캐노니컬 50% 진입 레벨
- `mitigation_extreme: float` — 미티게이션 체크에 사용되는 전체 윅 원점 극단; bullish OB = `bar[ob_index].low`(윅); bearish OB = `bar[ob_index].high`(윅); `use_body_only`와 무관하게 항상 독립적
- `ts: datetime` — OB 캔들의 타임스탬프
- `bos_ts: datetime` — 해당 OB를 검증한 구조 돌파/변위 캔들의 타임스탬프
- `strength: float` — STEP 9에서 계산된 품질 점수 0.0–1.0(선택적으로 STEP 13의 HTF 컨플루언스로 부스트)
- `has_fvg: bool` — OB 캔들과 변위 캔들 사이에 비-겹침 Fair Value Gap(bullish: 캔들 3 저점 > 캔들 1 고점; bearish: 캔들 3 고점 < 캔들 1 저점)이 존재하는지 여부
- `mitigated: bool` — OB 캔들의 전체 윅 원점 극단(`mitigation_extreme`)을 종가로 돌파한 이후 True
- `mitigation_ts: datetime or None` — 처음으로 미티게이션을 트리거한 바의 타임스탬프
- `liquidity_swept: bool` — 미티게이션 이벤트 이전 또는 중에 이전 스윙 저점(bullish OB) 또는 스윙 고점(bearish OB)을 스윕했으면 True; `is_breaker=True` 지정의 전제조건
- `is_breaker: bool` — OB가 전체 윅 극단 종가 돌파로 미티게이션되고 `liquidity_swept=True`이면 True; 캐노니컬 Breaker Block으로 전환됨. 미티게이션됐지만 `liquidity_swept=False`이면 실패 OB / mitigation block이며 `is_breaker`는 False 유지.
- `breaker_direction: str or None` — `'bearish_breaker'`(전 bullish OB, 이제 저항) 또는 `'bullish_breaker'`(전 bearish OB, 이제 지지); `is_breaker=True`일 때만 설정
- `breaker_retest_ts: datetime or None` — 미티게이션 후 가격이 반대 방향에서 breaker 존에 처음 재진입하는 바의 타임스탬프
- `htf_confluence: bool` — 이 OB 존이 활성(미티게이션 안 됨) HTF OB와 겹치면 True(멀티-타임프레임 모드에서만; 그 외 False)
- `oi_confirmation: bool` — 방향적 확약을 확인하는 변위 바에서 OI가 증가하면 True(크립토/선물 전용; 그 외 False)
- `visited: bool` — 가격이 처음으로 진입 목표 OB 존에 재진입한 이후 True(bullish: `bar[p].low <= zone_high`; bearish: `bar[p].high >= zone_low`); 각 바에서 미티게이션 체크 이전에 설정됨

**진입 관련성** —

Order Block을 사용한 진입 타이밍 로직은 세 단계로 작동한다.

**PHASE 1 — 대기 (OB 미재테스트 상태):** OB 형성(BOS 확정, OB 등록) 후 즉시 진입하지 않는다. 가격은 OB 존에서 멀어지는 변위 중이다. OB 존 방향으로 되돌아오는 풀백/되돌림(pullback/retracement)을 기다린다.

**PHASE 2 — 경계 존 (가격이 OB 재진입, visited=True 설정 중):** `bar[p].low <= zone_high`(bullish OB) 또는 `bar[p].high >= zone_low`(bearish OB)일 때 OB 진입 존이 터치된다. 이것이 진입 창이다. 정밀도/공격성에 따른 세 가지 서브-진입 레벨: (a) **공격적(Aggressive):** `zone_high`(bullish) 또는 `zone_low`(bearish) 최초 터치 시 진입 — 스탑을 `mitigation_extreme`에 타이트하게 설정, 최고 R:R; (b) **표준(Standard):** `zone_mid`(OB 보디/존의 50%)에서 진입 — 캐노니컬 ICT 진입, 정밀도와 체결 확률 균형; (c) **보수적/OTE(Conservative/OTE):** BOS 스윙 원점에서 변위 피크까지 피보나치를 적용; 0.62–0.79 되돌림이 OB 존 내에 있으면 해당 레벨에서 진입. 모든 진입의 손절매: OB 캔들의 전체 윅 극단인 `mitigation_extreme`에 소규모 ATR 버퍼(0.25–0.5 × ATR[14])를 더한 위치에 설정 — 미티게이션을 구성하지 않는 윅으로 인한 조기 청산을 피하기 위해 `zone_low`(보디만)에 설정하지 않는다.

**PHASE 3 — 회피/무효화:** 다음의 경우 진입하지 않는다: (a) bullish OB에서 `bar[p].close < mitigation_extreme`(`mitigated=True` — 존이 이제 지지가 아닌 잠재적 breaker일 수 있음); (b) 강도 점수 < `min_strength_score`; (c) HTF 추세가 OB 방향에 역행(bullish OB 진입 시도 중 활성 HTF 하락 구조); (d) OB가 이미 방문(`visited=True`)됐지만 반전에 실패 — 두 번째 재테스트는 확률이 낮으며 두 번째 재테스트 감소 규칙에 따라 강도 점수에서 0.15를 차감; (e) 크립토에서 상위 타임프레임 OI가 bullish OB 재테스트 시 하락 중(bearish 다이버전스).

**BREAKER BLOCK 진입:** `is_breaker=True`이고 `breaker_retest_ts`가 설정 중일 때(가격이 반대 방향에서 존에 재진입), 원래 OB 방향의 역방향으로 진입 — bearish_breaker 재테스트에서 bearish 진입, bullish_breaker 재테스트에서 bullish 진입. 손절매: breaker 존 `mitigation_extreme`(bearish_breaker는 존 저점, bullish_breaker는 존 고점) 너머 0.5 × ATR(14). 참고: Breaker 진입은 `liquidity_swept=True`를 요구하며; 유동성 스윕 없이 OB가 실패했다면 저확률 셋업으로 취급하여 포지션 크기를 줄인다.

**컨플루언스** —

| 조건 | 방향 | 가중치 |
|------|------|--------|
| 미티게이션 안 된 미방문 OB, `strength >= 0.60`, `htf_confluence=True` | OB 방향 | 0.65 |
| 표준 OB, HTF 확인 없음 | OB 방향 | 0.45 |
| Breaker Block 재테스트(원 OB 방향의 역방향) | 역방향 | 0.30 |
| FVG 존 내 겹침 | OB 방향 | +0.10 |
| HTF OB 정렬 | OB 방향 | +0.10 |
| Kill Zone 세션 타이밍(런던/뉴욕 오픈) | OB 방향 | +0.05 |
| 변위 확인 시 상승 OI(크립토) | OB 방향 | +0.05 |

OB 신호는 SMC 진입 시스템에서 1차 구조적 앵커이며, 유동성 스윕 · 세션 시간 · FVG가 동시에 존재하는 멀티-신호 시스템에서 최대 컨플루언스 가중치 0.65를 가진다.

**거짓신호 가드** —

- **BOS 종가 확인 요구(윅 전용 아님):** 보디 종가 없이 스윙 고점 위 윅만 돌파하는 것은 유동성 스윕/스탑 헌트이며 구조적 돌파가 아니다 — 윅 전용 BOS에서 OB를 등록하지 않는다.
- **변위 보디 필터 (`displacement_atr_mult >= 1.0`, Wilder ATR의 전체 True Range 계산):** 스윙 포인트를 종가로 닫는 소체 무브는 노이즈다. 1.0x ATR 최소 보디가 대부분의 거짓 BOS 이벤트를 제거한다. **참고:** ATR은 반드시 `TR = max(H-L, |H-prev_C|, |L-prev_C|)`와 Wilder Smoothing(`alpha=1/14`)을 사용해야 하며; `mean(H-L)` 단순 평균을 ATR로 사용하면 변동성을 과소평가하여 필터를 오교정한다.
- **마지막 역방향 캔들은 `ob_lookback_bars` 내에 있어야 함:** 마지막 역방향 캔들이 BOS 20+ 바 이전이라면 기관 주문 기억이 오래되어 존의 신뢰도가 낮다.
- **엔걸프먼트 체크 필수:** 변위 캔들 보디 종가가 임펄스 방향으로 OB 캔들의 **전체 윅** 극단을 초과해야 한다(`BOS_UP`: `close > bar[ob_i].high`; `BOS_DOWN`: `close < bar[ob_i].low`). 보디만의 부분 엔걸프먼트는 불충분하다.
- **역추세 OB 필터:** 지배적 HTF 추세에 역행하는 OB는 훨씬 낮은 유지율을 보인다. 마지막 HTF BOS 방향이 OB 방향에 역행하면 `htf_counter=True`로 플래그하고 가중치를 줄이거나 완전히 필터링한다.
- **미티게이션된 OB 제외:** `mitigated=True`(`mitigation_extreme` = 전체 윅 원점 극단 종가 돌파) 즉시 `active_obs`에서 제거. 미티게이션된 OB를 지지/저항으로 재테스트하는 것은 흔한 거짓 진입 함정이다.
- **두 번째 재테스트 감소:** 한 번 방문(`visited=True`)됐지만 유지된 OB는 두 번째 재테스트에 유효하되 강도를 0.15 줄인다. 세 번째 터치는 거의 항상 미티게이션으로 이어짐 — 매우 낮은 우선순위로 플래그하거나 건너뛴다.
- **OB 캔들 거래량 체크:** OB 캔들 거래량이 14바 평균의 50% 미만이면 기관 주문 체결 내러티브가 뒷받침되지 않음 — 강도 점수를 줄이거나 건너뛴다.
- **횡보/RANGING 시장에서 OB 회피:** `displacement_body / mean(이전 20바의 full_range)`를 측정. 비율이 1.5 미만이면 시장이 횡보 중일 수 있으며 OB의 기관 전제가 약화된다.
- **크립토 펀딩률 체크(선물):** perpetual futures에서 bearish OB 재테스트 시 `funding_rate > +0.10%`이면 OB가 롱 스퀴즈 역학에 압도될 수 있음 — 낮은 확신으로 처리한다.
- **라이브 모드에서 룩어헤드 편향 가드:** STEP 2의 스윙 포인트는 오른쪽에 `swing_lookback` 확인 바가 필요하다. 스트리밍/라이브 모드에서 `bar[swing_index + swing_lookback]`이 종가를 형성할 때까지 스윙을 emit하지 않는다. 이를 강제하지 않으면 최신 바에서 phantom 스윙과 phantom BOS 이벤트가 발생한다.

**함정** —

- **ATR 공식 오류:** 흔한 구현 버그는 ATR을 N바의 `mean(bar.high - bar.low)`(high-low 범위의 단순 평균)로 계산하는 것이다. 이는 `TR = max(H-L, |H-prev_C|, |L-prev_C|)` True Range 공식에서 갭 기여분을 **생략**하여 야간 갭이나 크립토 시간봉 갭 이후 변동성을 체계적으로 과소평가한다. 또한 캐노니컬 ATR은 단순 이동평균이 아닌 Wilder's Smoothing(`alpha = 1/period`)을 사용하며 — SMA-ATR과 Wilder-ATR은 기간 14에서 30–40% 차이가 난다.
- **존 정의 vs 미티게이션 경계 혼동:** `use_body_only=True`는 정밀한 진입 배치를 위해 더 타이트한 진입 목표 존(보디만)을 정의하지만, 미티게이션 경계는 **항상** 전체 캔들 윅 극단을 사용해야 한다(`bar.low` for bullish OB, `bar.high` for bearish OB). `use_body_only`를 미티게이션 체크에 적용하면 조기 무효화가 발생함 — 존에 윅이 침투하지만 전체 캔들 저점 미만으로 종가를 형성하지 않는 것은 ICT 캐노니컬에 의한 미티게이션이 아니다.
- **Breaker Block vs 실패 OB 혼동:** 캐노니컬 ICT Breaker Block은 OB 실패 전 **이전 유동성 스윕**을 요구한다. 스윕 없이 OB 극단 돌파가 발생하면 실패 OB(mitigation block)로 확률이 낮으며 — Breaker로 레이블해서는 안 된다. 둘을 혼동하면 거짓 breaker 신호가 팽창한다.
- **BOS vs CHoCH 모호성:** 상승 추세에서 BOS_UP은 추세 지속을 확인하고(OB가 수요 존으로 작용), 하락 추세에서 CHoCH_UP은 반전을 시사한다. 탐지 알고리즘은 두 가지 모두 변위 이벤트로 처리하지만 방향적 맥락이 다르므로 — 하위 필터링을 위해 각각 이전 추세 방향으로 레이블한다.
- **미티게이션 vs Breaker 혼동:** OB 극단을 통한 윅 침투가 종가 형성 없이 발생한 것은 미티게이션이 아니다 — 이는 유동성 탈취다. 많은 구현이 윅 터치에서 OB를 미티게이션됐다고 잘못 표시한다. 이 스펙은 ICT 캐노니컬에 따라 `close_mitigation=True`를 사용한다.
- **OB 캔들 선택의 최근 편향:** '마지막' 역방향 캔들 규칙은 변위 직전의 **단일** 가장 최근 역방향 캔들만을 OB로 지정함을 의미한다. 연속 역방향 캔들은 마지막 것에서만 하나의 OB를 생성한다. 일부 실무자는 전체 통합 범위를 '공급/수요 존'으로 사용하지만 — 이 스펙은 ICT 엄격 기준을 따른다: 마지막 단일 캔들만.
- **상품/타임프레임 간 ATR 기간 민감도:** Wilder smoothing을 사용한 ATR(14)은 변동성 높은 크립토 15분봉에서 일봉 외환과 매우 다른 절대 임계값을 생성한다. `displacement_atr_mult=1.0`은 조정이 필요할 수 있다: 크립토 인트라데이 ~0.7, 일봉 외환 ~1.5.
- **스윙 탐지에서 룩어헤드 편향:** `swing_lookback=2`는 2개의 미래 바가 확인 필요. 완전히 형성된 데이터의 백테스팅에서 스윙 포인트는 알고리즘이 확인을 지연시키지 않으면 '너무 일찍' 탐지된다. 라이브 시스템에서 오른쪽 바들이 종가를 형성할 때까지 스윙 포인트를 보류해야 한다.
- **주문서 데이터는 OB 탐지에 불필요:** OB는 OHLCV에서 파생된 순수 가격 패턴 개념이다. 주문서 데이터는 필수 입력에서 제거됐으며 캐노니컬 입력에 나열되지 않는 선택적 외부 강화다.
- **소체 정제 OB:** `use_body_only=True`이고 OB 캔들의 보디가 매우 작고 큰 윅이 있는 경우(`body_to_range_ratio < 0.25`), 진입 존이 현실적으로 체결하기에 너무 좁을 수 있다. 이런 캔들에 대해서는 `use_body_only=False`로 폴백하거나 `narrow_body` 경고 필드로 플래그하는 것을 고려한다.
- **Breaker Block 진입은 신선한 OB 진입보다 낮은 확률:** 원 OB 존의 기관 주문이 이미 소진됐으며; breaker는 갇힌 소매(trapped-retail) 개념을 나타내는데, 이는 신선한 OB 재테스트보다 경험적으로 덜 검증됐다.

**참고** —

- https://innercircletrader.net/tutorials/ict-order-block/
- https://innercircletrader.net/tutorials/ict-bullish-order-block/
- https://innercircletrader.net/tutorials/ict-bearish-order-block/
- https://innercircletrader.net/tutorials/ict-breaker-block-trading/
- https://innercircletrader.net/tutorials/ict-mitigation-block-explained/
- https://innercircletrader.net/tutorials/break-of-structure-bos/
- https://innercircletrader.net/tutorials/ict-market-structure-shift/
- https://innercircletrader.net/tutorials/ict-optimal-trade-entry-ote-pattern/
- https://innercircletrader.net/tutorials/valid-ict-fair-value-gap/
- https://atas.net/blog/what-are-ict-order-blocks-and-breaker-blocks-in-trading/
- https://fxnx.com/en/blog/ict-breaker-blocks-master-art-trading-failed-order-blocks
- https://github.com/joshyattridge/smart-money-concepts
- https://www.luxalgo.com/blog/ict-trader-concepts-order-blocks-unpacked/
- https://algostorm.com/ict-smc-key-concepts/
- https://tradingfinder.com/education/forex/ict-bearish-order-block/
- https://www.ictkillzone.com/ict-ote
- https://en.wikipedia.org/wiki/Average_true_range
- https://www.macroption.com/atr-calculation/
- https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-true-range-atr-and-average-true-range-percent-atrp
- https://tradingstrategyguides.com/day-12-breaker-blocks-mitigation-blocks-explained-ict-smc-deep-dive/
- https://tradingstrategyguides.com/day-6-fair-value-gaps-explained-ict-smc-fvg-trading-guide/

## 4. 유동성 (liquidity)

**정의** — 유동성(Liquidity)은 특정 가격 레벨 위아래에 집적된 대기 주문(pending order)의 집합체로, ICT/SMC 프레임워크에서는 "스마트 머니(smart money)"로 불리는 기관 알고리즘이 대형 포지션을 체결하기 위해 반드시 탈취해야 하는 연료(fuel)로 간주한다. 매수-측 유동성(Buy-Side Liquidity, BSL)은 스윙 고점·같은 높이의 고점(equal highs)·이전 세션 고점 위에 집적된 매수 스탑(buy stop) 주문군이며, 매도-측 유동성(Sell-Side Liquidity, SSL)은 스윙 저점·같은 높이의 저점(equal lows)·이전 세션 저점 아래의 매도 스탑(sell stop) 주문군이다. 유동성 스윕(liquidity sweep) 또는 스탑 헌트(stop hunt)는 가격의 윅(wick)이 BSL/SSL 레벨을 돌파하되 종가(close)는 레벨 안으로 되돌아오는(reclaim) 단일 바 또는 복수 바 패턴으로, 가격이 레벨을 돌파한 뒤 같은 방향으로 계속 닫히면 "유동성 런(liquidity run)"으로 구별한다. 딜링 레인지(dealing range)는 명확한 변위(displacement) 무브로 정의된 직전 스윙 고점-저점 구간이며, 이 범위의 피보나치 0.5 중간점(equilibrium)을 기준으로 상반(above 0.5)을 프리미엄(premium), 하반(below 0.5)을 디스카운트(discount) 존으로 분류한다. 최적 진입 존(Optimal Trade Entry, OTE)은 딜링 레인지 내 0.62~0.79 피보나치 되돌림 구간으로, 상승 설정에서는 디스카운트 OTE(레인지 저점 기준 0.62~0.79 되돌림), 하락 설정에서는 프리미엄 OTE(레인지 고점 기준 0.62~0.79 되돌림)에 해당하며, 스윕 이후 MSS(Market Structure Shift) 확인 후 이 구간에서 진입을 탐색한다.

**탐지 알고리즘** —

1. **STEP 1 — 유동성 풀 식별 (`identify_liquidity_pools`):** 시점 t까지 확정된 피벗(`bars[0 … t]`)만 사용. `시장구조 섹션(01_structure)`의 `identify_pivots(swing_left, swing_right)` 결과인 확정 스윙 고점 목록(`pivot_highs`)과 스윙 저점 목록(`pivot_lows`)을 입력으로 받는다. **Equal Highs(EQH) / BSL 풀 탐지:** `pivot_highs` 내 모든 피벗 쌍 `(pa, pb)` (`pa.ts < pb.ts`, 시간순)에 대해 `abs(pa.price - pb.price) / max(pa.price, pb.price) <= eq_tolerance_pct`를 만족하는 쌍을 그룹화한다. ≥ 2개의 피벗이 그룹을 형성하면 BSL 풀로 등록: `{price: max(그룹 가격들), side: 'BSL', touch_count: len(그룹), ts: 가장 최근 피벗 ts, zone_low: min(그룹 가격들), zone_high: max(그룹 가격들), mitigated: False}`. **Equal Lows(EQL) / SSL 풀 탐지:** `pivot_lows`에 동일 로직 적용(비교 기준 `min(그룹 가격들)` 사용), `side='SSL'`로 등록. **단독 스윙 극단 풀 등록:** EQH/EQL을 형성하지 못한 개별 스윙 고점 및 저점도 `touch_count=1`의 BSL/SSL 풀로 등록한다 — 단독 스윙 극단도 유동성 표적이 되기 때문이다. **타이 처리:** `max(pa.price, pb.price)`를 분모로 사용하여 피벗 도착 순서 무관 대칭성을 보장한다. `eq_tolerance_pct == 0`인 경우 완전 동일 가격만 매칭된다. **출력:** `pools: list[dict]` — 각 풀 딕셔너리에 `price`, `side`, `touch_count`, `ts`, `zone_low`, `zone_high`, `mitigated` 필드 포함.

2. **STEP 2 — 스윕 탐지 (`detect_liquidity_sweep`):** 시점 t의 현재 바 `b_t`에 대해, `mitigated=False`인 모든 BSL/SSL 풀을 순회한다. **BSL 스윕 조건:** `b_t.high > pool.price AND b_t.close <= pool.price`. 즉 윅이 풀 레벨을 상방 돌파했으나 종가가 레벨 이하로 되돌아온 경우. **SSL 스윕 조건:** `b_t.low < pool.price AND b_t.close >= pool.price`. 즉 윅이 풀 레벨을 하방 돌파했으나 종가가 레벨 이상으로 되돌아온 경우. **Reclaim 판정:** 스윕 확인 후 다음 바 `b_{t+1}`에서도 종가가 레벨 반대편 유지를 검증한다 — `b_{t+1}.close <= pool.price` (BSL 스윕 후) 또는 `b_{t+1}.close >= pool.price` (SSL 스윕 후)이면 `reclaimed=True`로 강화 확정. 단, 단일 바 스윕도 STEP 2 출력에 즉시 포함하되 `reclaimed` 필드에 현재 판정값을 기재한다. **스윕 종료 조건(BOS로 전환):** `b_t.close > pool.price` (BSL) 또는 `b_t.close < pool.price` (SSL) — 종가가 레벨을 완전히 돌파한 경우 스윕이 아니라 BOS로 분류하며, 해당 풀을 `mitigated=True`로 갱신한다. **출력:** `sweep: {ts, level, side('BSL'/'SSL'), bar_index, wick_extreme, reclaimed: bool, type: str(STEP 3에서 채움)}`.

3. **STEP 3 — 스윕 타입 분류 (`classify_sweep_type`):** STEP 2의 스윕 이벤트를 입력으로 받아 `type` 필드를 채운다. **(a) 단일-바 윅 스윕(Single-bar wick sweep / Liquidity Grab):** 스윕 감지 바 `b_t`의 윅 돌파 거리 `wick_extension = abs(b_t.high - pool.price)` (BSL) 또는 `abs(pool.price - b_t.low)` (SSL)가 `b_t`의 ATR(단순 True Range 대체: `max(b_t.high - b_t.low, abs(b_t.high - bars[t-1].close), abs(b_t.low - bars[t-1].close))`) 의 `sweep_reject_pct` 이내이면 → `type='single_bar_grab'`. **(b) 다중-바 스윕(Multi-bar sweep):** `b_t`가 레벨을 종가 돌파(`b_t.close > pool.price` for BSL)했으나 이후 `mss_lookback` 바 이내 `b_{t+k}.close`가 다시 레벨 아래로 내려온 경우 → `type='multi_bar_sweep'`. 이 유형은 현재 처리 바 t+k에서 소급 갱신(`retroactive update on same pool, not future bar`)한다 — 탐지 시점 `t+k`까지 확정 불가로 `reclaimed=False`에서 `reclaimed=True`로 갱신. **(c) 런(Liquidity Run):** 스윕 조건을 충족하지 못하고 `b_t.close`가 레벨을 초과하여 닫히면 → 스윕 아님, BOS로 처리. **lookahead 금지:** 다중-바 스윕 판정은 `t+k` 바가 종가를 확정한 시점에서만 이루어지며, `t+k`보다 미래 바 데이터는 어떤 필드에도 사용하지 않는다.

4. **STEP 4 — MSS 탐지 (`detect_mss`):** 유동성 스윕 이후 추세 반전의 구조적 확인. **입력:** STEP 2의 스윕 이벤트, 내부 구조 피벗(`int_pivot_highs`, `int_pivot_lows`). **MSS 조건:** SSL 스윕(잠재적 상승 반전) 이후 `mss_lookback` 바 이내에서 — (a) 내부 스윙 고점 `ih`이 스윕 바 `b_sweep` 이후 형성되고 (b) 이후 바 `b_mss`의 `b_mss.close > ih.price` (내부 스윙 고점의 종가 상방 돌파) — 를 충족하면 Bullish MSS 확인. BSL 스윕 이후에는 (a) 내부 스윙 저점 `il` 형성 후 (b) `b_mss.close < il.price`이면 Bearish MSS 확인. **변위 캔들 요건(선택적 강화):** MSS 확인 바 `b_mss`의 바디 크기 `abs(b_mss.close - b_mss.open)`이 해당 바 True Range의 `mss_body_ratio`(기본 0.5) 이상이면 변위 캔들로 추가 신뢰도 부여 — `displacement: True` 플래그. **윅 전용 돌파 거부:** `b_mss`의 `b_mss.high > ih.price`이지만 `b_mss.close <= ih.price`이면 MSS 아님; STEP 2의 유동성 스윕 이벤트로 분류. **출력:** `mss: {ts, direction('BULLISH'/'BEARISH'), broken_level, sweep_ts, bar_index, displacement: bool}`. MSS가 `mss_lookback` 바 이내에 감지되지 않으면 해당 스윕에 MSS 없음으로 처리.

5. **STEP 5 — 프리미엄/디스카운트 분류 (`classify_premium_discount`):** **딜링 레인지 선택:** 현재 바 `b_t` 이전에 유동성 스윕이 발생한 경우, 스윕 직전 형성된 가장 최근 스윙 고점 `dr_high`와 스윙 저점 `dr_low`를 딜링 레인지 경계로 사용한다. 레인지가 없거나 `dr_high == dr_low`이면 분류 불가(`price_zone='undefined'`). **계산:** `eq = dr_low + (dr_high - dr_low) * 0.5` (equilibrium, 0.5 중간점). **분류:** `current_price = b_t.close`. `current_price > eq + (dr_high - dr_low) * price_zone_eq_buffer`이면 `'premium'`; `current_price < eq - (dr_high - dr_low) * price_zone_eq_buffer`이면 `'discount'`; 그 외(buffer 범위 내) `'equilibrium'`. 기본 `price_zone_eq_buffer = 0.02` (레인지의 ±2%를 equilibrium 중간 구역으로 처리). **출력:** `{price_zone: str, eq: float, dr_high: float, dr_low: float}`.

6. **STEP 6 — OTE 존 계산 (`compute_ote_zone`):** **입력:** `dr_high`, `dr_low`(STEP 5), `direction`(BULLISH/BEARISH). **Bullish OTE (디스카운트 내 진입 존):** 피보나치를 레인지 저점(`dr_low`) → 고점(`dr_high`) 방향으로 표시. `ote_low_price = dr_high - (dr_high - dr_low) * ote_high` (0.79 되돌림 가격), `ote_high_price = dr_high - (dr_high - dr_low) * ote_low` (0.62 되돌림 가격), `ote_705_price = dr_high - (dr_high - dr_low) * 0.705`. **Bearish OTE (프리미엄 내 진입 존):** 피보나치를 레인지 고점(`dr_high`) → 저점(`dr_low`) 방향으로 표시. `ote_low_price = dr_low + (dr_high - dr_low) * ote_low` (0.62 되돌림 가격), `ote_high_price = dr_low + (dr_high - dr_low) * ote_high` (0.79 되돌림 가격), `ote_705_price = dr_low + (dr_high - dr_low) * 0.705`. **가드:** `dr_high <= dr_low`이면 OTE 계산 불가 — `ote_zone=None` 반환. **출력:** `ote_zone: {low: float, high: float, mid_705: float, direction: str, dr_high: float, dr_low: float}`.

**파라미터** —

| name | default | 의미 |
|------|---------|------|
| `eq_tolerance_pct` | `0.0015` | Equal Highs/Lows 판정 퍼센트 허용치: `abs(p1 - p2) / max(p1, p2) <= eq_tolerance_pct`. 기본 0.15%는 유동성 높은 대형주·주요 외환 기준(LuxAlgo EQH/EQL 구현 일치). 변동성 높은 알트코인은 0.05~0.08%, 저변동성 FX 세션은 0.25~0.30%로 조정. |
| `sweep_reject_pct` | `0.5` | 단일-바 스윕 판정 시 윅 돌파 거리가 해당 바 True Range의 이 비율 이하이면 `single_bar_grab`으로 분류. 0.5 기본 = 윅이 True Range의 절반 이내. |
| `mss_lookback` | `10` | 스윕 이후 MSS를 탐색하는 최대 바 수. 이 범위 내 MSS 미감지 시 해당 스윕은 MSS 없음으로 처리. |
| `mss_body_ratio` | `0.5` | MSS 확인 바의 바디 크기 / True Range 비율. 이 값 이상이면 `displacement=True`. 0.0으로 설정 시 모든 MSS에 displacement 플래그 부여. |
| `ote_low` | `0.62` | OTE 존 하단 피보나치 레벨. |
| `ote_high` | `0.79` | OTE 존 상단 피보나치 레벨. |
| `ote_705` | `0.705` | OTE 존 중심 피보나치 레벨(최고 확신 진입점). |
| `discount_threshold` | `0.5` | 딜링 레인지 내 equilibrium 구분 피보나치 레벨. 0.5 미만 = 디스카운트, 0.5 초과 = 프리미엄. |
| `price_zone_eq_buffer` | `0.02` | Equilibrium 중간 구역 폭. 레인지 대비 ±2% 이내를 `'equilibrium'`으로 분류하여 경계선 노이즈 방지. |
| `pool_lookback` | `50` | 유동성 풀을 탐색할 최대 확정 피벗 수. 이 범위를 초과한 미티게이션 안 된 풀은 스탤(stale)로 처리. |
| `min_touch_count` | `1` | BSL/SSL 풀로 등록하기 위한 최소 피벗 수. 기본값 1이면 단독 스윙도 등록. 2 이상 설정 시 Equal Highs/Lows만 등록(노이즈 감소, 민감도 하락). |

**출력 필드** —

- `pools: list[dict]` — 각 풀: `{price: float, side: 'BSL'|'SSL', touch_count: int, ts: date|datetime, zone_low: float, zone_high: float, mitigated: bool, mitigated_ts: date|datetime|None}`
- `sweeps: list[dict]` — 각 스윕: `{ts: date|datetime, level: float, side: 'BSL'|'SSL', wick_extreme: float, reclaimed: bool, type: 'single_bar_grab'|'multi_bar_sweep', bar_index: int}`
- `mss_events: list[dict]` — 각 MSS: `{ts: date|datetime, direction: 'BULLISH'|'BEARISH', broken_level: float, sweep_ts: date|datetime, bar_index: int, displacement: bool}`
- `ote_zone: dict|None` — `{low: float, high: float, mid_705: float, direction: 'BULLISH'|'BEARISH', dr_high: float, dr_low: float}` — 유효 딜링 레인지 없으면 `None`
- `price_zone: str` — `'premium'|'discount'|'equilibrium'|'undefined'`
- `eq_price: float|None` — 딜링 레인지 equilibrium 가격 (0.5 중간점)
- `dealing_range: dict|None` — `{high: float, low: float, ts_high: date|datetime, ts_low: date|datetime}`

**진입 관련성** —

유동성 탐지기는 방향성 필터(direction gate)와 진입 타이밍(entry timing) 두 레이어를 모두 제공한다.

1. **SSL 스윕 + Bullish MSS → 롱 진입 준비:** `sweeps[-1].side == 'SSL'` AND `mss_events[-1].direction == 'BULLISH'` AND `mss_events[-1].sweep_ts == sweeps[-1].ts` — 이 조건이 충족된 경우 롱 진입 탐색. 추가 조건: `price_zone == 'discount'`에서만 롱 진입; 프리미엄에서 SSL 스윕은 단기 되돌림에 불과할 가능성이 높다.

2. **BSL 스윕 + Bearish MSS → 숏 진입 준비:** `sweeps[-1].side == 'BSL'` AND `mss_events[-1].direction == 'BEARISH'` — 숏 진입 탐색. `price_zone == 'premium'`에서만 숏 진입; 디스카운트에서 BSL 스윕 후 숏 진입은 역추세 리스크.

3. **OTE 존 한정 진입:** MSS 확인 후 가격이 OTE 존(`ote_zone.low` ~ `ote_zone.high`)으로 되돌아오면 진입 대기. `ote_zone.mid_705`(0.705 레벨)에 근접할수록 우선 진입. OTE 존 위(bullish) 또는 아래(bearish)에서 이미 진행 중인 경우 추격 진입하지 않는다.

4. **Equilibrium에서 대기:** `price_zone == 'equilibrium'`일 때 방향성 진입을 보류. 가격이 프리미엄이나 디스카운트로 이동한 후 재평가.

5. **스윕 단독(MSS 미확인) → 대기:** 스윕이 발생했으나 `mss_lookback` 바 이내 MSS가 확인되지 않으면 진입하지 않는다 — 스윕이 런(run)으로 전환될 가능성을 배제하지 못한다.

**컨플루언스** —

| 조건 | 방향 | 가중치 |
|------|------|--------|
| SSL/BSL 스윕 + Bullish/Bearish MSS (displacement=True) | 스윕 반대 방향 | 0.30 |
| SSL/BSL 스윕 + Bullish/Bearish MSS (displacement=False) | 스윕 반대 방향 | 0.20 |
| 스윕 단독 (MSS 미확인, reclaimed=True) | 스윕 반대 방향 | 0.15 |
| 스윕 단독 (MSS 미확인, reclaimed=False) | 스윕 반대 방향 | 0.10 |
| price_zone == 'discount' (롱 바이어스) | 상승 | 0.10 |
| price_zone == 'premium' (숏 바이어스) | 하락 | 0.10 |
| 현재가 OTE 존 내 (ote_zone 유효) | 진입 방향 | 0.15 |
| touch_count >= 3 인 BSL/SSL 풀 스윕 | 스윕 반대 방향 | 추가 +0.05 |

스윕+MSS 기반 컨플루언스 최대치 0.30은 시장구조(BOS/CHoCH) 가중치 0.30~0.40과 동등한 수준으로 설계한다. 스윕+MSS 0.30과 시장구조 방향 일치(0.30)가 동시에 충족될 때 합산 기여도 최대 0.60으로, 단독 요소만으로 진입 임계값(0.65~0.70 권장)에 미달하므로 오더블록 또는 FVG 컨플루언스를 별도 추가해야 한다.

**거짓신호 가드** —

- **BOS vs 스윕 구분 가드:** `b_t.close > pool.price` (BSL) 또는 `b_t.close < pool.price` (SSL)이면 스윕이 아닌 BOS로 분류. 반드시 종가 reclaim이 확인되어야 스윕으로 등록한다. 윅만 돌파하고 종가가 레벨 반대편을 유지하면 BOS이므로 해당 풀을 `mitigated=True`로 갱신하고 스윕 카운트에 포함하지 않는다.
- **저유동성 가드:** `pool.touch_count == 1`이고 해당 피벗의 `volume < rolling_20_avg_volume * 0.3`이면 해당 풀의 스윕 신호 가중치를 절반으로 낮춘다 — 저거래량 피벗은 기관 스탑 집적이 미약하다.
- **스탤 풀 가드:** 풀 형성 이후 `pool_lookback`개 바를 초과해도 미티게이션이 발생하지 않으면 스탤(stale) 풀로 플래그 설정하고 신규 스윕 탐지에서 제외. 오래된 풀에 대한 스윕은 최신 구조적 의미를 갖지 않는다.
- **딜링 레인지 정합성 가드:** `dr_high == dr_low` 또는 `dr_high < dr_low`이면 OTE와 프리미엄/디스카운트 계산을 수행하지 않고 `price_zone='undefined'`, `ote_zone=None`을 반환.
- **MSS 위크-온리 가드:** MSS 확인 바 `b_mss`의 종가가 내부 스윙 레벨을 돌파하지 못하고 윅만 돌파한 경우 MSS로 계산하지 않는다 — 내부 구조 스윕으로 기록.
- **다중-바 스윕 소급 갱신 제한:** `type='multi_bar_sweep'`의 `reclaimed=True` 갱신은 현재 처리 바 `t+k`의 종가 확정 이후에만 수행. `t+k`보다 미래 바의 데이터를 참조하여 사전에 reclaim 여부를 판단하는 로직은 룩어헤드 바이어스이며 금지.
- **OTE 내 가격 = 진입 충분 조건 아님:** OTE 존 진입만으로는 진입 신호를 발화하지 않는다. 반드시 스윕+MSS 이후 OTE 도달 조건을 순서대로 충족해야 한다.

**함정** —

- **스윕 후 계속 진행(acceptance) vs 반전 혼동:** 스윕 이후 가격이 같은 방향으로 다음 바에서도 종가를 형성하면(`b_{t+1}.close > pool.price` for BSL sweep) 스윕이 런으로 전환된 것이다. `reclaimed`를 실시간으로 모니터링하여 진입 신호를 취소해야 한다. "가격이 레벨 너머로 계속 닫히고 풀백이 얕으면 이는 수용(acceptance)이며 스윕이 약한 롱의 털어내기였다"는 원칙을 코드 수준에서 구현한다.
- **딜링 레인지 선택의 임의성:** OTE와 프리미엄/디스카운트 계산에 사용하는 딜링 레인지는 타임프레임과 어느 스윙을 기준으로 삼느냐에 따라 크게 달라진다. 동일 종목에서 일봉 딜링 레인지와 4시간 딜링 레인지가 다른 OTE를 산출하는 경우, 하이어 타임프레임(HTF) 딜링 레인지를 방향성 필터로, 로어 타임프레임(LTF) OTE를 진입 타이밍으로 사용하는 계층적 접근이 ICT 캐노니컬 방법이다.
- **Equal Highs/Lows 허용치 민감도:** 너무 넓은 `eq_tolerance_pct`는 독립적인 스윙 극단을 하나의 풀로 묶어 노이즈 스윕 신호를 과다 생산한다. 너무 좁으면 정확히 같은 가격이 아닌 한 풀을 등록하지 못한다. 변동성 조건과 타임프레임에 따라 파라미터를 조정할 것.
- **MSS lookback 창 길이 vs 신속 진입:** `mss_lookback`이 짧으면 반전 초기 진입 기회를 잡지만 가짜 반전이 많다. 길면 신뢰도가 높지만 진입이 늦어져 OTE 존을 이미 통과한 경우가 발생한다. 타임프레임별 최적값: 일봉 3~5, 4시간 5~10, 15분 10~20.
- **프리미엄에서 롱, 디스카운트에서 숏 진입 금지 원칙의 예외:** 매크로 추세가 강한 경우 프리미엄 존 내 되돌림에서도 롱 진입이 유효할 수 있다. 그러나 단기 스캘핑 타임프레임에서는 프리미엄/디스카운트 규칙을 엄격 적용하는 것이 거짓 신호를 줄인다.
- **`touch_count=1` 풀의 낮은 신뢰도:** 단독 스윙 극단은 기관 스탑 집적의 증거가 약하다. `touch_count >= 2`(Equal Highs/Lows) 풀의 스윕이 `touch_count=1` 풀 스윕보다 반전 확률이 통계적으로 높다 — 컨플루언스 가중치를 차등 적용하는 이유.
- **크립토 한정 보조입력(order_book / open_interest):** `order_book` 데이터가 제공되는 경우 BSL 레벨 위의 대형 매도 호가 집중(ask wall) 또는 SSL 레벨 아래의 대형 매수 호가 집중(bid wall)이 스윕 방향 확인에 도움을 준다. `open_interest`가 스윕 바 전후 급감하면 스탑 실제 체결(이탈 포지션 청산)을 나타내어 `reclaimed=True` 신뢰도를 높인다. 이 두 입력은 없어도 알고리즘이 동작하며, 있으면 스윕 `type` 분류와 컨플루언스 가중치 보정에만 사용한다.

**참고** —

- https://innercircletrader.net/tutorials/ict-liquidity-pool/
- https://innercircletrader.net/tutorials/liquidity-in-forex-trading/
- https://innercircletrader.net/tutorials/ict-liquidity-sweep-vs-liquidity-run/
- https://innercircletrader.net/tutorials/ict-optimal-trade-entry-ote-pattern/
- https://innercircletrader.net/tutorials/ict-fibonacci-levels/
- https://innercircletrader.net/tutorials/ict-premium-and-discount-zone-identification/
- https://innercircletrader.net/tutorials/ict-market-structure-shift/
- https://www.luxalgo.com/blog/market-structure-shifts-mss-in-ict-trading/
- https://tradingfinder.com/education/forex/ict-bsl-ssl/
- https://theforexgeek.com/ict-buy-side-liquidity-and-sell-side-liquidity/
- https://www.equiti.com/sc-en/news/trading-ideas/liquidity-sweeps-explained-how-to-identify-and-trade-them/
- https://fxnx.com/en/blog/ict-dealing-range-map-institutional-moves
- https://tradingstrategyguides.com/day-3-smc-ict-market-structure-explained-bos-choch-swing-points-2026/
- https://github.com/joshyattridge/smart-money-concepts
- https://pypi.org/project/smartmoneyconcepts/

## 5. 매물대 (볼륨프로파일) (volume_profile)

**정의** — 볼륨프로파일(Volume Profile)은 선택한 룩백 윈도우 내 거래량을 이산 가격 구간(bin)별로 수평 히스토그램으로 시각화한 분석 도구로, 시장 참여자들이 어느 가격대에서 합의를 이뤘는지를 보여 준다. 단일 구간 중 누적 거래량이 가장 높은 가격을 컨트롤 포인트(POC, Point of Control)라 하며, 해당 세션의 '공정 가치(fair value)' 앵커 역할을 한다. 밸류 에어리어(VA, Value Area)는 POC 주변의 연속 가격 구간 중 전체 세션 거래량의 설정 비율(표준 70% — 정규분포 1표준편차 68.27%의 실용적 반올림)을 포함하는 영역이며, 위쪽 경계를 VAH(Value Area High), 아래쪽 경계를 VAL(Value Area Low)이라 한다. 이 영역은 단일행(single-row) CME 정렬 확장 알고리즘으로 산출한다. 고거래량 노드(HVN, High Volume Node)는 세션 최대 대비 임계값 이상의 히스토그램 피크로, 가격이 감속되는 강력한 지지/저항 자석 역할을 한다. 저거래량 노드(LVN, Low Volume Node)는 반대로 임계값 이하의 얇은 골짜기로, 가격이 마찰 없이 빠르게 통과하는 구간이다. 나이키드/버진 POC(NPOC/VPOC)는 이후 가격 움직임이 한 번도 거래되지 않은 이전 세션 POC로, 미티게이션(mitigation)되기 전까지 중력적 인력을 유지한다. B형 프로파일은 두 개의 D형(종형 곡선) 분포가 LVN 골짜기로 분리된 진정한 이중봉(bimodal) 구조를 의미하며, 단순히 상하 균형 분포가 아니다 — 각 하위 분포는 자체적인 국소 거래량 피크를 가져야 한다.

**탐지 알고리즘**

1. **프로파일 윈도우 수집.** `bars = list[PriceBar]`에서 `profile_window_bars` 개수만큼 선택한다. 세션 프로파일은 대상 날짜의 전체 바(장중), 또는 일봉 N-바 롤링 윈도우를 사용한다. `bar.ts` 기준 오름차순 정렬. `global_low = min(b.low for b in bars)`, `global_high = max(b.high for b in bars)`. 미래 바 참조 금지 — 모든 바는 `ts <= current_bar.ts`이어야 한다(룩어헤드 바이어스 방지).

2. **가격 구간(bin) 정의.** `price_range = global_high - global_low`. `price_range == 0`이면 단일 구간 처리(`num_bins=1`, `bin_size=0`, `vol_bins=[sum(b.volume for b in bars)]`)하고 이후 단계 3~7 건너뜀(`poc_price=global_low`). `bin_size = price_range / num_bins` (기본: 장중 100, 일봉 50). 각 구간 `i`에 대해 `bin_low[i] = global_low + i * bin_size`, `bin_high[i] = bin_low[i] + bin_size`. 마지막 구간의 `bin_high[num_bins-1]`은 부동소수점 오차 시 `global_high`로 강제 보정. `vol_bins = [0.0] * num_bins` 초기화.

3. **각 바의 거래량을 겹치는 구간에 균등 배분.** 각 바 `b`에 대해 `span = b.high - b.low`. `span == 0`이면 `b.close` 위치 구간에 전체 거래량 추가 후 다음 바로. `span > 0`이면 각 구간 `i`에 대해 `overlap = min(b.high, bin_high[i]) - max(b.low, bin_low[i])`. `overlap > 0`이면 `vol_bins[i] += b.volume * (overlap / span)`. 주의: 균등 H-L 분포는 근사치이며, 실제 서브바 거래량은 시가/종가 근처에 집중된다(함정 참조).

4. **POC 식별.** `poc_idx = argmax(vol_bins)` (최대값 동률 시 가장 낮은 인덱스 선택 — 보수적 기준). `poc_price = bin_low[poc_idx] + bin_size * 0.5` (구간 중간값). `total_vol = sum(vol_bins)`.

5. **밸류 에어리어 계산 (단일행 CME 정렬 확장).** 주의: CME 표준 단일행 방식은 매 반복마다 위 1구간 vs 아래 1구간을 비교하며, 두 구간 쌍을 비교하는 Trading Technologies X_STUDY 방식과 다르다. `target_vol = total_vol * value_area_pct` (기본 0.70). `accumulated = vol_bins[poc_idx]`, `va_lo_idx = poc_idx`, `va_hi_idx = poc_idx`. `accumulated < target_vol`인 동안: `above_vol = vol_bins[va_hi_idx+1]` (한계 초과 시 -1.0), `below_vol = vol_bins[va_lo_idx-1]` (한계 미만 시 -1.0). `above_vol >= below_vol`이고 `above_vol >= 0`이면 `va_hi_idx += 1`, `accumulated += above_vol`. 그렇지 않고 `below_vol > above_vol`이고 `below_vol >= 0`이면 `va_lo_idx -= 1`, `accumulated += below_vol`. 동률 시 위쪽 확장(시카고 곡물거래소 관례). `VAH = bin_high[va_hi_idx]`, `VAL = bin_low[va_lo_idx]`. 모든 구간 소진 시 자연 종료. `va_pct_actual = accumulated / total_vol` 보고(이산 구간 경계로 인한 초과 시 유용).

6. **HVN·LVN 감지.** `session_max_vol = max(vol_bins)`. 각 구간 `i`에 대해 `vol_pct = vol_bins[i] / session_max_vol * 100`. `vol_pct >= hvn_threshold`(기본 80.0)이면 HVN, `vol_pct <= lvn_threshold`(기본 20.0)이면 LVN. 동일 레이블의 연속 구간을 클러스터로 묶어 `node_low`, `node_high`, `node_vol`, `node_mid = (node_low + node_high) / 2` 기록. `node_mid`는 출력 필드에 필수이므로 반드시 포함한다.

7. **프로파일 형태 분류.** 윈도우 내 바가 10개 미만이면 `shape='D'`(데이터 부족). `poc_rel = (poc_price - global_low) / (global_high - global_low)`. `va_center = (VAH + VAL) / 2`, `va_center_rel = (va_center - global_low) / (global_high - global_low)`. `close_rel = (bars[-1].close - global_low) / (global_high - global_low)`. 형태 규칙은 순서대로 적용한다: (1) **B형 먼저 검사** (P/b/D 오분류 방지). `vol_bins`의 국소 최대값(양쪽 이웃보다 크고 `/ session_max_vol >= bimodal_peak_threshold`)을 `peaks`로 수집. `len(peaks) >= 2`이면 연속 쌍 `(peaks[j], peaks[j+1])`에 대해 골짜기 최소값 `valley_vol`이 `session_max_vol * lvn_threshold/100` 이하이고, 각 하위 분포가 `total_vol`의 10% 이상이면 `shape='B'`. 단, 상하 질량 비율로 판단하는 구방식은 균형(balance)을 감지할 뿐 이중봉이 아니므로 사용 금지. (2) **P형**: `poc_rel >= poc_shape_upper_threshold`(기본 0.60) AND `va_center_rel >= 0.55` AND `close_rel >= 0.50`이면 `shape='P'`. (3) **b형**: `poc_rel <= poc_shape_lower_threshold`(기본 0.40) AND `va_center_rel <= 0.45` AND `close_rel <= 0.50`이면 `shape='b'`. (4) **기본**: `shape='D'`. 파라미터 참고: `poc_shape_upper_threshold`와 `poc_shape_lower_threshold`를 Step 7에서 명시적으로 참조해야 한다 — 0.60/0.40 하드코딩 시 파라미터가 무효화된다.

8. **나이키드/버진 POC 감지 (세션 간).** `naked_pocs = list[(price: float, formed_ts)]`를 최대 `lookback_sessions`(기본 20)개 완료 프로파일에서 시드한다. 새 세션 프로파일 POC가 목록에 없으면 추가. 현재 세션의 각 바 `b`를 처리할 때 각 `(npoc_price, formed_ts)`에 대해 `b.low <= npoc_price <= b.high`이면 미티게이션 — 목록에서 제거, `mitigation_ts = b.ts` 기록. 스탤니스 가드: `(current_bar.ts - formed_ts).days > lookback_sessions * 2`이면 스탤(stale) 처리 후 제거.

9. **출력 조합.** 아래 출력 필드 항목을 참조한 딕셔너리 반환.

**파라미터** —

| name | default | 의미 |
|---|---|---|
| `num_bins` | `100` | 프로파일 윈도우 H-L 범위를 나누는 등폭 가격 구간 수. 장중(15m-1h)은 100, 일봉은 50. 20 미만은 중요 노드 병합, 500 초과는 노이즈 과적합. |
| `value_area_pct` | `0.70` | 밸류 에어리어에 포함해야 하는 전체 세션 거래량 비율. CME 표준 70%(정규분포 1σ = 68.27%의 실용적 반올림). |
| `hvn_threshold` | `80.0` | 세션 최대 대비 구간 거래량이 이 값 이상(%)이면 HVN으로 분류. 일반 범위 65~95%. |
| `lvn_threshold` | `20.0` | 세션 최대 대비 구간 거래량이 이 값 이하(%)이면 LVN으로 분류. 일반 범위 15~25%. |
| `bimodal_peak_threshold` | `0.50` | B형 감지 시 국소 최대값이 `session_max_vol` 대비 이 비율 이상이어야 구조적 피크로 인정. 노이즈 스파이크 방지. |
| `profile_window_bars` | `390` | 하나의 프로파일로 집계하는 바 수. 390 = 미국 주식 1m 1세션; 크립토 4h 기준 일간 프로파일은 96. 종목·타임프레임별 설정 필수. |
| `lookback_sessions` | `20` | NPOC 전달을 위해 스캔하는 완료 세션 수. 스탤니스 기준: `lookback_sessions * 2` 달력일 초과 시 NPOC 폐기. |
| `poc_shape_upper_threshold` | `0.60` | `poc_rel >= 이 값`이면 P형 분류(POC가 범위 상단). Step 7에서 반드시 파라미터로 참조. |
| `poc_shape_lower_threshold` | `0.40` | `poc_rel <= 이 값`이면 b형 분류(POC가 범위 하단). Step 7에서 반드시 파라미터로 참조. |

**출력 필드** —

- `ts: datetime` — 프로파일 윈도우 마지막 바의 타임스탬프
- `poc_price: float` — 최고 거래량 구간의 가격 중간값(POC)
- `vah: float` — 밸류 에어리어 상단 경계(`bin_high[va_hi_idx]`)
- `val: float` — 밸류 에어리어 하단 경계(`bin_low[va_lo_idx]`)
- `va_pct: float` — 사용된 `value_area_pct` 파라미터(예: 0.70)
- `va_pct_actual: float` — 실제 포함된 `total_vol` 비율 (이산 구간 경계로 목표치 초과 가능; 퇴화된 플랫 프로파일 감지에 유용)
- `shape: str` — `'D'`(대칭 종형), `'P'`(상단 불마켓), `'b'`(하단 베어마켓), `'B'`(이중봉, 두 D형이 LVN으로 분리)
- `poc_rel: float` — 범위 하단 기준 POC의 정규화 위치 [0, 1]
- `hvn_nodes: list[dict]` — 각 딕셔너리: `node_low`, `node_high`, `node_vol`, `node_mid`
- `lvn_nodes: list[dict]` — 각 딕셔너리: `node_low`, `node_high`, `node_vol`, `node_mid`
- `naked_pocs: list[tuple]` — 각 튜플: `(npoc_price: float, formed_ts: datetime)` — 미티게이션되지 않은 이전 세션 POC
- `total_vol: float` — 모든 구간에 걸친 총 거래량
- `bin_size: float` — 각 구간의 가격 폭
- `vol_bins: list[float]` — 구간별 거래량 배열 (길이 = `num_bins`)
- `global_low: float` — 프로파일 가격 범위 하단
- `global_high: float` — 프로파일 가격 범위 상단

**진입 관련성** — 볼륨프로파일은 다섯 가지 진입 타이밍 방식으로 활용된다. (1) **POC 리테스트** — 가격이 위에서 `poc_price`에 닿거나 아래서 터치한 후 1~3바 거부 반응(위크가 POC로 진입, 캔들 바디는 반대 방향 마감)을 기다려 거부 바 극단값 돌파 시 진입. (2) **VAH/VAL 브레이크아웃·리테스트** — 평균 이상 거래량으로 VAH 위(VAL 아래) 종가 형성 시 VAH(VAL)를 새 지지(저항)로 설정; 해당 레벨을 지지하는 첫 풀백 캔들에서 롱(숏) 진입. (3) **밸류 에어리어 페이드** — ADX < 20 저변동 레인징 조건에서 밸류 에어리어 외부로 이동 후 내부로 종가 회귀는 평균회귀 신호; POC 방향으로 진입, VAH/VAL 너머에 스톱, POC→반대 VA 경계를 목표로. (4) **NPOC 자석** — 가격이 NPOC로부터 1 ATR 이내 접근 시 고확률 타겟; 추세 방향 일치 시 NPOC 레벨로 진입, NPOC 도달 시 청산; 바 범위가 `npoc_price`에 겹치면 목록에서 제거 후 재평가. (5) **LVN 가속·HVN 감속** — LVN 구간 통과 중에는 페이드 금지; HVN까지 모멘텀 라이딩. HVN에서는 감속 예상; 이익 실현 기준 또는 포지션 축소. HVN에서 모멘텀에 역행한 진입은 흡수·반전 위험으로 회피. **형태별 맥락**: P형 → VAL 지지 롱 선호; b형 → VAH 저항 숏 선호; D형 → 양쪽 극단에서 평균회귀 선호; B형 → 두 하위 분포 중 지배적 POC를 먼저 판별 후 방향 결정.

**컨플루언스** —

| 조건 | 방향 | 가중치 |
|---|---|---|
| `shape='P'`, `poc_rel >= poc_shape_upper_threshold`, 현재가 VAH 위 or VAL 반등, 2 ATR 이내 NPOC 없음 | 강세 (Bullish) | 0.65 |
| `shape='b'`, `poc_rel <= poc_shape_lower_threshold`, 현재가 VAL 아래 or VAH 거부 | 약세 (Bearish) | 0.65 |
| `shape='D'` 또는 `'B'`, 밸류 에어리어 내 위치, 방향성 브레이크아웃 없음 | 중립 (Neutral) | 0.30 |

프로파일 기준 전체 모듈 컨플루언스 가중치: **0.55** (프로파일이 신선할 때, 5세션 이내). 20세션 이상 경과 또는 `vol_bins` 분산이 높은 얇은 시장 조건에서는 0.30으로 하향. B형: 상위 피크 거래량이 클 경우 상위 VAL에서 강세 선호; 하위 피크 지배 시 하위 VAH에서 약세 선호.

**거짓신호 가드** —

- **얇은 프로파일 가드**: `(global_high - global_low) / bar_atr_20 < 0.5`이면 의미 있는 거래량 분포 없는 갭앤고 바로 판단; 형태 분류·HVN/LVN 레이블링 건너뜀.
- **저거래량 가드**: `total_vol < 0.3 × rolling_20_session_avg_vol`이면 비유동성 프로파일 — 진입 신호 생성 금지.
- **단일 스파이크 가드**: `max(vol_bins) / total_vol > 0.60`(한 구간에 전체의 60% 이상 집중)이면 70% 밸류 에어리어가 극소 범위로 붕괴; 해당 세션 `value_area_pct`를 0.85로 확장하거나 퇴화 프로파일로 표시.
- **NPOC 스탤니스 가드**: `(current_ts - npoc_formed_ts).days > lookback_sessions * 2`이면 NPOC 폐기.
- **형태 경계 가드**: 프로파일 윈도우 바 수 < 10이면 `shape='D'`로 분류(데이터 불충분).
- **B형 검증 가드**: 식별된 두 거래량 피크가 각각 `total_vol`의 10% 이상이어야 하며, 미달 시 D형으로 기본 처리. 두 피크 사이 골짜기가 확인된 LVN(`vol_pct <= lvn_threshold`)이어야 한다.
- **밸류 에어리어 경계 가드**: `va_pct_actual > value_area_pct + 0.10`이면(이산 구간으로 10% 포인트 초과) `va_pct_actual`에 불일치 기록하고 `num_bins` 절반으로 축소 검토.
- **퇴화 균일분포 가드**: `(max(vol_bins) - min(vol_bins)) / max(vol_bins) < 0.10`이면(거래량이 전 구간 거의 균일) 구조적 의미 없음; HVN/LVN/형태 신호 모두 억제, `shape='D'` 플래그 보고.

**함정** —

- **OHLCV 균등 H-L 분포는 근사치**: 실제 거래량은 시가/종가 서브바 근처에 집중된다. 틱 데이터 사용 시 POC가 상당히 이동할 수 있다. 종가 가중 근사치(close position weight 0.5 + 균등 0.5) 고려 가능하나, 비표준이므로 플랫폼 간 재현성이 보장되지 않는다.
- **밸류 에어리어 알고리즘 변형 핵심 주의**: (1) 표준 단일행 CME/TradingView 방식 — 위 1구간 vs 아래 1구간 비교 후 더 큰 쪽 추가; (2) Trading Technologies X_STUDY 두-행 방식 — 다음 두 구간의 합 비교. 두 구현은 비대칭 분포에서 몇 구간씩 차이를 낼 수 있다. 브로커 게시 VAH/VAL와 맞추려면 해당 플랫폼의 알고리즘 변형을 먼저 확인하라.
- **구간 수 민감도**: 구간 수 < 20이면 중요 노드 병합, > 500이면 노이즈 과적합. 일봉 OHLCV의 균형은 50구간; 서브시간 데이터는 100 권장. 구간 크기는 `va_pct_actual` 초과에도 영향을 미친다.
- **세션 경계 정의**: 크립토(24/7 시장)에는 자연 세션이 없으므로 UTC 자정, 주봉 시가, 사용자 지정 스윙 등으로 앵커를 외부에서 정의해야 한다. 앵커 미정렬 시 POC 값이 무의미해진다.
- **NPOC 거짓 자석**: 비정상적으로 높은 변동성(실적, 매크로 이벤트) 중 형성된 NPOC는 정상 조건에서 재테스트되지 않을 수 있다. 해당 세션 `total_vol`이 20세션 평균의 2배 이상이면 NPOC 신호 가중치 낮춤.
- **P형 vs D형 혼동**: D형 프로파일은 세션 후반 매수 압력에 따라 P형으로 변형될 수 있다. 장중 사용 시, 예상 세션 거래량의 80% 이상 도착 후에만 형태 분류.
- **VAH/VAL을 경직된 지지/저항으로 과신 금지**: 외환·크립토 얇은 시장에서는 거래량이 추정치이므로(OKX/Binance는 명목 기준 보고, 틱 카운트 아님) 확률적 구간으로 취급.
- **HVN/LVN 임계값은 플랫폼마다 상이**: 기본값 80/20(세션 최대 대비 백분위)은 PhenLabs TradingView 구현 기준이다. NinjaTrader, StrategyQuant 등은 절대 틱 카운트 또는 구성 가능한 행 수를 사용한다. 실제 배포 전 해당 종목의 거래량 분포에 맞춰 임계값 교정 필수.
- **B형 비율 방식 오감지**: 상하 질량 비율로 균형을 측정하면 균형 D형이 B형으로 잘못 분류된다. NinjaTrader·QuantVue 정의에서 B형은 LVN 골짜기로 분리된 두 거래량 피크를 요구한다.
- **`poc_shape_upper_threshold`·`poc_shape_lower_threshold` 무효화 주의**: Step 7에서 이 파라미터를 명시적으로 참조하지 않고 0.60/0.40을 하드코딩하면 파라미터 변경이 형태 분류에 전혀 영향을 주지 않는다.

**참고** —

- https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/
- https://gocharting.com/docs/orderflow/volume-profile-charts
- https://tradingtechnologies.com/blog/2013/05/15/volume-at-price/
- https://ninjatrader.com/futures/blogs/trade-futures-understanding-the-4-common-volume-profile-shapes/
- https://www.trader-dale.com/how-to-read-volume-profile-shapes-what-the-market-is-really-telling-you/
- https://www.mypivots.com/dictionary/definition/158/virgin-point-of-control-vpoc
- https://www.mypivots.com/dictionary/definition/442/naked-point-of-control-npoc
- https://www.oanda.com/us-en/trade-tap-blog/trading-knowledge/volume-profile-explained/
- https://npfinancials.com.au/volume-profile/
- https://www.futureshive.com/blog/volume-profile-trading-strategy-2025
- https://www.tradingview.com/script/RvNPu7jq-LVN-HVN-Auto-Detection-PhenLabs/
- https://www.pyquantlab.com/article.php?file=An+Algorithmic+Exploration+of+Enhanced+Volume+Profile+with+Python+and+Backtrader.html
- https://www.quantconnect.com/forum/discussion/7716/working-out-value-area-a-k-a-market-profile-volume-profile-from-auction-market-theory/
- https://optionstradingiq.com/volume-profile/
- https://internationaltradinginstitute.com/blog/reading-the-volume-profile-from-acceptance-to-rejection/
- https://www.quantvps.com/blog/value-area-trading-strategy-guide
- https://www.quantvue.io/post/shapes-of-volume-profiles
- https://medium.com/@beinghorizontal/market-profile-value-area-calculations-with-nifty-future-as-an-example-c6264526a536
- https://www.chartspots.com/volume-profile-strategy-profiting-from-naked-vpoc-levels/
- https://quantcrawler.com/learn/volume-profile

---

## 6. 볼륨분석 (volume_analysis)

**정의** — 볼륨분석(Volume Analysis)은 각 가격 움직임 뒤에 있는 시장 참여 품질을 측정하는 가격-거래량 기법의 집합으로, 진정한 기관적 의지(institutional commitment)와 저확신·고갈된 움직임을 구분한다. 누적 압력 지표(OBV, A/D Line), 오실레이터형 플로우 게이지(CMF), 바 수준의 법의학적 방법(VSA)을 포괄한다. 핵심 전제는 Wyckoff의 노력 대 결과 법칙(Law of Effort vs. Result)으로, 지속 가능한 가격 움직임은 상응하는 거래량(노력)을 필요로 하며, 두 요소 간의 다이버전스는 기관의 흡수(absorption), 분배(distribution), 또는 소진(exhaustion)을 신호한다. 파생 신호로는 현재 활동을 롤링 기준선과 정규화하는 상대거래량 비율(RVOL), 클라이맥스 탑/바텀(극단적 거래량 + 넓은 스프레드 + 특정 종가 위치), 볼륨 드라이업(연속 감소 거래량), 가격 레벨에서 전문적 관심 부재를 노출하는 VSA 노-디맨드/노-서플라이 바가 있다. 크립토 선물에서는 미결제약정(OI) 변화와 가격 방향을 결합해 롱 빌드업, 숏 빌드업, 숏 커버링, 롱 언와인드의 4가지 포지셔닝 체제(regime)를 분류한다.

**탐지 알고리즘**

1. **전제 조건.** `bars: list[PriceBar]`는 `ts` 기준 오름차순 정렬. 최소 길이 = `max(obv_ema_period, rvol_period, cmf_period) + divergence_lookback + 5`. 모든 보조 상수는 파라미터 섹션 참조. `bar.volume >= 0`.

2. **롤링 평균 거래량 계산 (RVOL·VSA 기준선).** 각 바 `i`(`i >= rvol_period-1`)에 대해 `avg_vol[i] = statistics.mean(bar.volume for bar in bars[i-rvol_period+1 : i+1])`. `i < rvol_period-1`이면 `avg_vol[i] = None` (해당 바의 모든 탐지기 건너뜀).

3. **상대거래량 (RVOL).** `rvol[i] = bars[i].volume / avg_vol[i]` (`avg_vol[i]`이 None 아니고 > 0일 때). 분류: `rvol < 0.5` → `'dry_up'`; `0.5 <= rvol < 1.5` → `'normal'`; `1.5 <= rvol < 3.0` → `'elevated'`; `3.0 <= rvol < 4.0` → `'spike'`; `rvol >= 4.0` → `'climax'`(StockCharts 표준 극단 스파이크 임계값).

4. **바 수준 스프레드·종가 위치 계산 (VSA용).** `spread[i] = bars[i].high - bars[i].low`. `i >= spread_lookback-1`이면 `avg_spread[i] = statistics.mean(...)`, `spread_pct[i] = spread[i] / avg_spread[i]`. 스프레드 분류: `< 0.6` → `'narrow'`; `0.6~1.5` → `'medium'`; `> 1.5` → `'wide'`. 종가 위치(`close_loc[i]`): `rng = bars[i].high - bars[i].low`. `rng == 0`이면 `'mid'`. `(bars[i].close - bars[i].low) / rng >= 0.7`이면 `'upper'`; `<= 0.3`이면 `'lower'`; 그 외 `'mid'`. 바 방향: `up_bar[i] = bars[i].close > bars[i].open`; `down_bar[i] = bars[i].close < bars[i].open`.

5. **OBV (On-Balance Volume).** `obv[0] = bars[0].volume`. `i > 0`: 종가 상승 → `obv[i] = obv[i-1] + bars[i].volume`; 하락 → `obv[i] = obv[i-1] - bars[i].volume`; 동일 → `obv[i] = obv[i-1]`(동률 바: Granville 원조). EMA 스무딩: `k = 2 / (obv_ema_period + 1)`. `obv_ema[0] = obv[0]`; `obv_ema[i] = obv[i]*k + obv_ema[i-1]*(1-k)`. 정밀도를 높이려면 첫 `obv_ema_period`개 OBV 값의 SMA로 시드하는 방법 사용.

6. **ADL (Accumulation/Distribution Line).** 각 바 `i`: `rng = bars[i].high - bars[i].low`. `rng == 0`이면 `clv = 0.0`(평봉 가드). 그 외 `clv = ((bars[i].close - bars[i].low) - (bars[i].high - bars[i].close)) / rng`. `mfv[i] = clv * bars[i].volume`. `adl[0] = mfv[0]`; `adl[i] = adl[i-1] + mfv[i]`.

7. **CMF (Chaikin Money Flow).** `i >= cmf_period-1`: `window_mfv = sum(mfv[j] for j in range(i-cmf_period+1, i+1))`; `window_vol = sum(bars[j].volume ...)`; `cmf[i] = window_mfv / window_vol` (`window_vol > 0`이면). 신호 분류: `cmf[i] > cmF_bull_threshold`(기본 +0.05) → `'bullish'`; `cmf[i] < -cmf_bear_threshold`(기본 -0.05) → `'bearish'`; `abs(cmf[i]) > 0.25` → `'strong'` 바이어스.

8. **VSA 노-디맨드 바.** `i >= 2` 필요. `no_demand[i] = True` 조건 전체: (a) `up_bar[i] == True`; (b) `spread_pct[i] < 0.6`(좁은 스프레드); (c) `bars[i].volume < min(bars[i-1].volume, bars[i-2].volume)`; (d) `close_loc[i] in ('mid', 'lower')`(종가가 상단 1/3에 있지 않음 — VSA 표준 조건). 확인 신호(lookahead 주의): `no_demand_confirmed[i] = no_demand[i] AND bars[i+1].close < bars[i].close`. 확인된 신호는 바 `i+1`이 가용될 때 바 `i`에 귀속되어 방출한다. 최소 1바 지연 필수.

9. **VSA 노-서플라이 바.** `no_supply[i] = True` 조건 전체: (a) `down_bar[i] == True`; (b) `spread_pct[i] < 0.6`; (c) `bars[i].volume < min(bars[i-1].volume, bars[i-2].volume)`; (d) `close_loc[i] == 'upper'`(기본 조건: 저가에서 강하게 반등). `close_loc[i] == 'mid'`는 약한 부가 신호; `no_supply_weak`으로 별도 플래그. `no_supply_confirmed[i] = no_supply[i] AND bars[i+1].close > bars[i].close`. 바 `i+1` 이후 방출, 1바 지연.

10. **VSA 노력 대 결과 분류.** 4가지 원형 분류. EvR은 Step 3의 `rvol_class` 레이블이 아닌 수치 RVOL 임계값을 사용함에 주의. `rvol_val = rvol[i]`, `spread_ratio = spread_pct[i]`. **HIGH_EFFORT_LOW_RESULT**(약세 — 전문적 저항): `rvol_val >= 2.0 AND spread_ratio < 0.7`; 레이블 = `up_bar[i]`이면 `'absorption'`, 아니면 `'selling_pressure_test'`. **LOW_EFFORT_HIGH_RESULT**(연속/소진): `rvol_val < 0.7 AND spread_ratio > 1.4`; 레이블 = `up_bar[i]`이면 `'effortless_rise'`, 아니면 `'effortless_fall'`(지지 없음, 약세). 그 외: `'neutral'`.

11. **거래량 스파이크·클라이맥스 감지.** **클라이맥스 탑**(매수 클라이맥스): (a) `rvol[i] >= climax_rvol_threshold`(기본 3.0); (b) `spread_pct[i] > 1.4`; (c) `up_bar[i] == True`; (d) `close_loc[i] in ('mid', 'lower')`(고점 유지 실패 — 분배 확인, 표준 조건); (e) `bars[i].close > max(bars[j].close for j in range(i-climax_lookback, i))`(N바 신고가). **클라이맥스 바텀**(매도 클라이맥스): (a) `rvol[i] >= climax_rvol_threshold`; (b) `spread_pct[i] > 1.4`; (c) `down_bar[i] == True`; (d) `close_loc[i] == 'upper'`(표준 Wyckoff 조건 — 저가에서 강하게 반등). `'mid'`는 약한 부가 신호; 원하면 `climax_bottom_weak` 별도 플래그; (e) `bars[i].close < min(bars[j].close for j in range(i-climax_lookback, i))`(N바 신저가). 수정 사항: 구방식에서 `climax_bottom`에 `close_loc in ('mid', 'upper')`를 허용했으나, 표준 Wyckoff/ATAS는 저가에서 확실히 벗어난 상단 종가를 요구한다. `'mid'`만으로는 과도한 거짓신호가 발생한다.

12. **볼륨 드라이업 (VDU) 통합 패턴.** `vdu_zone_end[i] = True` 조건: 윈도우 `[i-vdu_bars+1 .. i]`에 대해 (a) 모든 `bars[j].volume < avg_vol[j] * vdu_vol_threshold`(각 바가 기준선의 50% 미만); (b) 윈도우 내 최소 `vdu_bars-1`개 바에서 `volume[j] <= volume[j-1]`(감소 추세); (c) 가격 범위 수축: 후반부 최대 스프레드 < 전반부 최대 스프레드; (d) `rvol[i] < 0.5`(마지막 바로 드라이업 확인).

13. **OBV·ADL 다이버전스 (룩어헤드 바이어스 수정 포함).** 표준 피벗 감지는 `bars[i-1]`, `bars[i]`, `bars[i+1]`을 필요로 하므로, 바 `i`를 처리할 때 `bars[i+1]`은 미래 데이터다. 수정: 인덱스 `p`의 피벗은 바 `p+1`이 관측된 후에만 확인 가능 — 모든 다이버전스 신호는 최소 1바 지연으로 방출. 고점 피벗 `p`: `bars[p].close > bars[p-1].close AND bars[p].close > bars[p+1].close` (단, `p <= len(bars)-2`). 저점 피벗 `p`: `bars[p].close < bars[p-1].close AND bars[p].close < bars[p+1].close`. 현재 바 `i`에서 `[i-divergence_lookback, i-1]` 구간의 확인된 피벗을 수집(바 `i-1`이 최신 확인 가능 피벗). **약세 OBV 다이버전스**: 최근 2개 확인된 고점 피벗 `p1 < p2`에서 `bars[p2].close > bars[p1].close`(가격 고고점) AND `obv_ema[p2] < obv_ema[p1]`(OBV 저고점) → `obv_divergence = 'bearish'`. **강세 OBV 다이버전스**: 최근 2개 확인된 저점 피벗에서 가격 저저점 AND OBV 고저점 → `obv_divergence = 'bullish'`. ADL도 같은 로직으로 `adl_divergence` 계산. 다이버전스 선언 전 최소 2개 확인된 피벗 쌍 필요.

14. **(크립토 강화 — 선택) 오더북 불균형.** `ccxt` 오더북 스냅샷(`bids=[[price,size],...], asks=[[price,size],...]`)이 사용 가능한 경우: `top_n = ob_depth_levels`(기본 10); `bid_vol = sum(size for price,size in bids[:top_n])`; `ask_vol = sum(size for price,size in asks[:top_n])`; `ob_imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)` → [-1, +1]; `ob_bias = 'buy_pressure'` (`> ob_imbalance_threshold`), `'sell_pressure'` (`< -ob_imbalance_threshold`), 그 외 `'neutral'`.

15. **(크립토 강화 — 선택) 미결제약정 델타.** `oi_history`(바 종가 타임스탬프와 정렬된 `{ts, oi, mark_price}` 목록)가 사용 가능한 경우: `oi_delta_pct[i] = (oi[i] - oi[i-1]) / oi[i-1]`. 4사분면 레이블(업계 표준): OI 증가 + 가격 상승 → `'long_buildup'`(신규 롱 유입, 강세 확신); OI 증가 + 가격 하락 → `'short_buildup'`(신규 숏 유입, 약세 확신); OI 감소 + 가격 상승 → `'short_covering'`(숏 청산, 강세이나 구조적 아님); OI 감소 + 가격 하락 → `'long_unwind'`(롱 청산, 약세 소진). 수정: 구방식에서 OI 감소 + 가격 상승을 `'short_squeeze'`로 명명했으나, 올바른 포지셔닝 사분면 명칭은 `'short_covering'`이다. 숏 스퀴즈(short squeeze)는 시장 이벤트/과정이며, OI 사분면만으로는 식별 불가능하다.

16. **바별 출력 조합.** 각 바 `i`에 대해 아래 출력 필드 항목을 포함한 딕셔너리 반환.

**파라미터** —

| name | default | 의미 |
|---|---|---|
| `rvol_period` | `20` | RVOL·VDU 거래량 기준선 롤링 윈도우(바). StockCharts 표준은 50; 장중 반응성을 위해 20 선호. |
| `obv_ema_period` | `20` | 다이버전스 감지 전 OBV 노이즈 감소를 위한 EMA 스무딩 기간. |
| `cmf_period` | `21` | CMF 합산 룩백 기간. Chaikin 원조 명세는 21일(거래일 기준 1개월); 많은 플랫폼이 20으로 단축. 표준에 맞게 21 사용. |
| `cmf_bull_threshold` | `0.05` | 매수 압력 확인 CMF 임계값(StockCharts 제로라인 버퍼). |
| `cmf_bear_threshold` | `0.05` | 매도 압력 확인 절대 CMF 임계값(`CMF < -0.05`). |
| `spread_lookback` | `14` | VSA 스프레드 분류에 사용하는 평균 바 스프레드 계산 롤링 윈도우. |
| `climax_rvol_threshold` | `3.0` | 클라이맥스 이벤트 최소 RVOL. 4.0 이상은 극단 스파이크(StockCharts 기준); 3.0은 넓은 클라이맥스 구간. 종목·타임프레임별 교정 필수. |
| `climax_lookback` | `20` | 클라이맥스 탑/바텀의 신고/신저가 요건 체크 시 참조하는 이전 바 수. |
| `vdu_bars` | `5` | VDU 구역 선언을 위한 거래량 감소·임계값 미만 연속 최소 바 수. |
| `vdu_vol_threshold` | `0.50` | VDU 윈도우 내 각 바의 거래량이 롤링 평균의 이 비율 미만이어야 함(TradingSim VDU 정의 기준 50%). |
| `divergence_lookback` | `30` | OBV/ADL 다이버전스 감지 시 확인된 가격 피벗을 탐색하는 바 수. 피벗 확인에는 이 윈도우 너머로 1바 추가 필요(룩어헤드 수정). |
| `ob_depth_levels` | `10` | 불균형 비율 계산을 위한 오더북 스냅샷 당 사이드별 상위 N 가격 레벨(크립토 전용). |
| `ob_imbalance_threshold` | `0.20` | `ob_imbalance` 절대값이 이 값을 초과하면 방향성 매수/매도 압력으로 분류. |

**출력 필드** —

`ts`, `symbol`, `freq`, `rvol`, `rvol_class`, `spread_pct`, `spread_class`, `close_loc`, `obv`, `obv_ema`, `obv_divergence`, `adl`, `adl_divergence`, `adl_gap_distortion`, `cmf`, `cmf_signal`, `no_demand`, `no_demand_confirmed`, `no_supply`, `no_supply_weak`, `no_supply_confirmed`, `evr_label`, `climax_top`, `climax_bottom`, `climax_bottom_weak`, `vdu_zone_end`, `ob_imbalance`, `ob_bias`, `oi_context`

**진입 관련성** — 볼륨분석은 우선순위 순으로 진입 타이밍 추천기에 다음과 같이 기여한다.

1. **WAIT 신호 — 클라이맥스 탑/바텀**: `climax_top=True` 또는 `climax_bottom=True`이면 2차 테스트(통상 5~15바 후)가 나타날 때까지 대기. 매도 클라이맥스 저점에서 `rvol_class in ('normal','dry_up')` AND `spread_class='narrow'`로 2차 테스트가 확인될 때만 롱 진입 고려 — 클라이맥스가 진짜임을 검증하는 조건.

2. **롱 진입 트리거 — 노-서플라이 확인**: `no_supply_confirmed=True`(`close_loc='upper'`) AND `cmf_signal='bullish'` AND `rvol_class in ('normal','elevated')` → 최고 확신 롱 타이밍. 확인 바(i+1 상승)가 진입 캔들. `no_supply_weak`(`close_loc='mid'`)는 낮은 확신 변형으로 추가 확인 필요.

3. **숏 진입 트리거 — 노-디맨드 확인**: `no_demand_confirmed=True` AND `cmf_signal='bearish'` AND `rvol_class in ('normal','elevated')` → 숏 진입 타이밍. 확인 바가 진입 캔들.

4. **클라이맥스 바에서 직접 진입 회피**: `rvol_class='climax'` 단독, 후속 2차 테스트 패턴 없이는 클라이맥스 방향으로 진입 금지 — 소진 위험 고조.

5. **VDU 브레이크아웃 셋업**: `vdu_zone_end=True`인 통합 후, 다음 바에서 `rvol_class >= 'elevated'` AND `close_loc='upper'`(롱 기준)이면 평균 이상 확신의 브레이크아웃 진입 트리거.

6. **OBV/ADL 다이버전스 — 조기 경보, 직접 진입 아님**: `obv_divergence='bullish'` 또는 `adl_divergence='bullish'`는 롱 셋업을 준비 상태(WAIT)로 만들지만, 바 수준 확인 신호(노-서플라이 또는 VDU 브레이크아웃) 없이는 실제 진입 불가. 다이버전스 단독 = WAIT 상태. 피벗 확인 메커니즘으로 인해 최소 1바 지연 신호 유의.

7. **CMF 제로라인 크로스 확인**: CMF가 +0.05 위로(강세) 또는 -0.05 아래로(약세) 크로스하면 추세 중 인트라바 확인을 추가하나, 가격 구조 맥락 없이 독립 진입 트리거로 사용 불가.

8. **EvR 흡수**: `evr_label='absorption'`(상승 추세 중 거래량 높고 스프레드 좁은 상승 바)은 기관의 매도 저항을 신호 — 롱 회피, 숏 셋업 고려.

9. **크립토 OI 컨텍스트**: `oi_context='long_buildup'`은 롱 진입 확신 상승; `'short_covering'`은 숏에 주의(커버링 소진 후 반전 가능); `'long_unwind'`는 롱에 주의.

**컨플루언스** — 볼륨분석은 **확인/검증** 지표다 — 독립적으로 방향을 생성하지 않고 가격 구조 탐지기(오더 블록, 지지/저항, ICT FVG 등)의 신호를 증폭하거나 억제한다.

| 신호 | 방향 | 가중치 |
|---|---|---|
| 노-디맨드 확인 (약세) | Bearish | 0.75 |
| 노-서플라이 확인 `close_loc='upper'` (강세) | Bullish | 0.75 |
| 노-서플라이 약형 `close_loc='mid'` (강세) | Bullish | 0.55 |
| 클라이맥스 탑 + 2차 테스트 확인 (약세) | Bearish | 0.80 |
| 클라이맥스 바텀 `close_loc='upper'` + 2차 테스트 확인 (강세) | Bullish | 0.80 |
| 클라이맥스 바텀 약형 `close_loc='mid'` + 2차 테스트 | Bullish | 0.60 |
| VDU 브레이크아웃 (방향: 브레이크아웃 방향) | 방향성 | 0.65 |
| OBV 강세 다이버전스 | Bullish | 0.50 |
| ADL 강세 다이버전스 | Bullish | 0.45 |
| CMF > +0.05 (강세) | Bullish | 0.40 |
| EvR 흡수 (약세) | Bearish | 0.55 |
| 오더북 불균형 (크립토, 방향성) | 방향성 | 0.35 |

전체 모듈 가중치(멀티팩터 진입 추천기): **0.55–0.65** (강력한 보조 신호; 기본 가중치는 가격 구조/추세에 배정).

**거짓신호 가드** —

- **평봉/제로 범위 바**: `high == low`이면 CLV, ADL, CMF 계산 건너뜀(영(0) 나눗셈). `mfv = 0`, `close_loc = 'mid'` 설정.
- **비유동 종목**: `avg_vol < min_avg_volume_guard`(예: 1,000 단위)이면 RVOL가 무의미 — 모든 VSA·RVOL 출력 건너뜀, `rvol_class = 'undefined'`.
- **강한 추세에서의 OBV 다이버전스**: 강한 추세는 외견상 다이버전스(가격 상승, OBV 횡보)를 만들 수 있다. 가드: `divergence_lookback` 윈도우 내 최소 2개 확인된 피벗 쌍 필요.
- **데이터 피드 갭에서의 클라이맥스 오분류**: 거래 중단 또는 갭 오픈 첫 바의 넓은 스프레드·높은 거래량은 클라이맥스가 아니다. 가드: `bars[i].open`이 `bars[i-1].close`에서 3% 이상 갭이면 클라이맥스 감지에서 해당 바 제외.
- **갭 일자에서의 CMF 왜곡**: ADL(및 CMF)은 갭 다운 후 인트라바 중간 위상 위에서 종가를 기록하는 날 플로우를 오표현할 수 있다. `abs(bars[i].open - bars[i-1].close)/bars[i-1].close > 0.01`이면 `adl_gap_distortion=True`.
- **강한 상승 추세에서의 노-디맨드 바**: 강한 기관 축적 단계의 좁은 스프레드·저거래량 풀백은 노-디맨드처럼 보이지만 건강한 휴식이다. 가드: 20바 OBV 기울기가 평탄하거나 하락 방향일 때만 노-디맨드를 거래 신호로 사용.
- **크립토 상시 저거래량 기간 (주말 등)에서의 VDU 거짓 트리거**: 같은 요일 평균으로 거래량을 정규화한다.
- **갭 바에서의 EvR 흡수**: 갭 업 오픈 후 좁은 이후 스프레드는 HIGH_EFFORT_LOW_RESULT를 거짓 트리거할 수 있다. 가드: EvR 스프레드 측정은 인트라바 범위(high-low)만 사용.
- **숏 커버링을 강세 확신으로 오독**: `oi_context='short_covering'`은 `'long_buildup'`과 다르다. 숏 커버링은 잔여 미결제 숏이 소진되면 종료되며, 이 시점에 진입한 롱은 구조적 뒷받침이 없다.
- **`climax_bottom_weak`(`close_loc='mid'`) 거짓양성**: 가속 하락 추세에서의 중간 종가 하락 바는 기관 흡수를 반드시 의미하지 않는다. 반전 신호로 처리하기 전 후속 상승 바(2차 테스트) 최소 1개 필요.

**함정** —

- **OBV 시작값은 임의적**: OBV는 누적 지표로 절대 스케일이 없다. 종목 간·시간 윈도우 간 OBV 절대값 비교 금지 — 기울기와 다이버전스만 의미 있다.
- **ADL vs OBV 불일치는 예상되는 정보**: OBV는 종가 대 종가 비교, ADL은 인트라바 종가 위치를 사용한다. 갭 일자에서 크게 벌어질 수 있다. 두 지표가 모두 다이버전스에 동의하면 신호가 더 강하다.
- **CMF 표준 기간은 21, 20 아님**: Chaikin 원조 명세는 21일. 많은 플랫폼이 20으로 구현. 배포 전 플랫폼 기본값 확인.
- **노-디맨드/노-서플라이 확인 바**: VSA 문헌(ATAS)은 다음 바가 반대 방향으로 종가를 기록할 때까지 패턴이 미확인임을 명시한다. 패턴 바에서 즉시 신호를 방출하는 구현은 거짓양성률이 크게 높아진다. 모든 확인 신호는 최소 1바 지연을 가진다.
- **피벗 기반 다이버전스의 내재적 룩어헤드**: 표준 피벗 정의(bar[i]가 bar[i+1] 데이터 필요)는 라이브 시스템에서 1바 지연 없이는 구현 불가능하다. 이를 강제하지 않으면 백테스트 과적합이 실거래로 이전되지 않는다.
- **OI 사분면 'short_covering' ≠ 'short_squeeze'**: 가격 상승 + OI 하락은 표준적인 '숏 커버링'(질서 있는 청산). 숏 스퀴즈는 추가적인 속도/거래량 증거가 있는 극단적·고속 변형이다. 이 사분면을 'short_squeeze'로 표기하면 확신을 과장해 잘못된 진입 신호를 생성한다.
- **클라이맥스 거래량 임계값은 맥락 의존적**: 3.0x·4.0x RVOL 임계값은 시장·타임프레임에 따라 다르다. 크립토는 뉴스 시 RVOL > 5x 흔함; 주식 저유동 종목은 RVOL > 10x도 발생. `climax_rvol_threshold`는 종목별 교정 필수.
- **RVOL 기간이 VDU에 미치는 영향**: 50바 롤링 평균(StockCharts 기준)은 변동 시장에서 VDU 트리거를 어렵게 만든다; 20바(권장)는 더 반응적이나 정상적인 저변동성 기간에서의 거짓 드라이업 레이블이 많을 수 있다.
- **스프레드 정규화**: VSA 문헌은 '좁음' 측정을 표준화하지 않는다. 여기서 사용하는 롤링 평균 방식(`spread_lookback=14`)은 실무 관례이지 공개된 표준이 아니다. 일부 구현은 20~80백분위 기준을 사용한다.
- **EvR 원형은 독립적인 수치 임계값 사용**: EvR의 RVOL 임계값 2.0은 `'elevated'` `rvol_class` 버킷(1.5~3.0) 안에 속하는, 더 세밀한 컷포인트다. EvR 임계값(2.0·0.7)은 합성 근사치이며 Wyckoff/VSA 문헌에 정확한 수치가 없다. 대상 시장에서 백테스팅 필수.
- **OI 데이터 정렬**: `ccxt`의 OI 데이터 주기는 시간 단위 이하일 수 있다. 1일 OHLCV 바와 시간별 OI를 바 종가 타임스탬프에 맞춰 정렬해야 한다. OI 스냅샷 하나만 어긋나도 `oi_context` 분류가 역전된다.

**참고** —

- https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/on-balance-volume-obv
- https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/accumulation-distribution-line
- https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/chaikin-money-flow-cmf
- https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-volume-rvol
- https://en.wikipedia.org/wiki/On-balance_volume
- https://atas.net/volume-analysis/basics-of-volume-analysis/vsa-and-cluster-analysis-no-demand-and-no-supply/
- https://atas.net/volume-analysis/basics-of-volume-analysis/vsa-and-cluster-analysis-buying-and-selling-climax-patterns/
- https://www.tradingsim.com/blog/vdu-and-pocket-pivots
- https://www.earnforex.com/guides/volume-spread-analysis/
- https://tradingwyckoff.com/en/climax/
- https://tradingwyckoff.com/en/selling-climax/
- https://www.wyckoffanalytics.com/wyckoff-method/
- https://www.warriortrading.com/relative-volume-day-trading-terminology/
- https://www.5paisa.com/finschool/course/options-scalping-course/open-interest-spurts-and-4-quadrants/
- https://www.tradejini.com/blogs/how-to-interpret-open-interest-and-price-data-a-traders-guide
- https://corporatefinanceinstitute.com/resources/equities/chaikin-money-flow-cmf/
- https://wyckoffsmi.com/wyckoff-glossery/

## 7. 와이코프 매집/분산 (wyckoff)

**정의** — 와이코프 방법론(Wyckoff Method)은 대형 기관 투자자(Composite Operator, CO: 복합 운영자)가 소매 투자자의 감정적 반응을 활용해 가격을 체계적으로 조종하며 대량 포지션을 구축(매집, Accumulation) 또는 청산(분산, Distribution)하는 캠페인을 기술하는 프레임워크다. 매집 스키매틱(Accumulation Schematic)은 선행 하락 추세의 종료 이후 CO가 저렴한 가격에 대량 주식·코인을 흡수하는 TR(Trading Range, 거래 레인지) 구조를 페이즈 A~E 다섯 단계로 모형화하며, 분산 스키매틱(Distribution Schematic)은 선행 상승 추세의 종료 이후 CO가 보유 포지션을 고가에 일반 대중에 넘기는 TR 구조를 동일하게 페이즈 A~E로 기술한다. 두 스키매틱의 핵심 분석 도구는 '노력 대 결과(Effort vs. Result)' 법칙으로, 거래량(노력)과 가격 이동(결과)이 조화를 이루면 현재 방향 지속을, 불일치(대량거래-소폭 이동 또는 소량거래-대폭 이동)하면 잠재적 추세 반전을 암시한다. CO 개념은 특정 단일 주체가 아니라 수많은 기관·헤지펀드·마켓메이커의 집합적 행동을 하나의 가상 행위자로 추상화한 허리스틱 장치로, "가격 차트가 CO의 의도를 인코딩한 지도"라는 관점으로 해석한다.

**탐지 알고리즘** —

모든 단계는 입력 `bars: list[PriceBar]`를 `ts` 기준 오름차순으로 정렬한 뒤, 인덱스 `i = 0`(가장 오래된 바)부터 순차 처리한다. 시점 t에서의 탐지는 반드시 `bars[0 … t]`만 참조한다(no-lookahead). 피벗 확정 지연(confirmation lag)은 STEP 0에서 정의된다. 모든 분기 조건은 확정된 바에서만 평가한다.

### STEP 0 — 전제 조건 및 공통 통계

```
bars = sorted(bars, key=lambda b: b.ts)
N = len(bars)
i ← 0 ... N-1 (처리 순서)

# 볼륨 Z-스코어 (롤링 climax_vol_lookback 바 기준)
def vol_zscore(i, bars, lookback):
    window = [bars[j].volume for j in range(max(0, i-lookback+1), i+1)]
    if len(window) < 2:
        return 0.0
    mean_v = statistics.mean(window)
    std_v  = statistics.stdev(window)
    return (bars[i].volume - mean_v) / std_v if std_v > 0 else 0.0

# 바 스프레드(spread) = high - low
spread(i) = bars[i].high - bars[i].low

# 바 body 크기 = |close - open|

# 볼륨 이동평균 (vol_ma_period 바)
vol_ma(i) = mean(bars[j].volume for j in range(max(0, i-vol_ma_period+1), i+1))

# 스프레드 이동평균 (spread_ma_period 바)
spread_ma(i) = mean(spread(j) for j in range(max(0, i-spread_ma_period+1), i+1))

# 피벗 high/low 탐지 (swing_lookback=pivot_lookback, 미래 편향 방지)
# 인덱스 i의 피벗은 bars[i+pivot_lookback]이 닫힌 후에만 확정
def is_pivot_high(i, bars, pivot_lookback):
    if i < pivot_lookback or i > N - pivot_lookback - 1:
        return False
    return all(bars[i].high >= bars[j].high
               for j in range(i-pivot_lookback, i+pivot_lookback+1) if j != i)

def is_pivot_low(i, bars, pivot_lookback):
    if i < pivot_lookback or i > N - pivot_lookback - 1:
        return False
    return all(bars[i].low <= bars[j].low
               for j in range(i-pivot_lookback, i+pivot_lookback+1) if j != i)
```

---

### STEP 1 — 볼륨 클라이맥스(SC / BC) 탐지 — TR 후보 하단/상단 형성

**목적**: TR의 하단 경계(매집의 경우 SC = Selling Climax)와 상단 경계(분산의 경우 BC = Buying Climax)를 식별하여 이후 이벤트의 기준 레벨을 설정한다.

**Selling Climax (SC) 탐지 조건 (매집 TR):**
```
# 후보 조건 (바 i에서 평가):
cond_sc_vol   : vol_zscore(i, bars, climax_vol_lookback) >= climax_volume_zscore
                # 예: Z ≥ 2.0 → 롤링 lookback 내 평균 대비 2σ 이상 대량거래
cond_sc_price : bars[i].close < bars[i-1].close  (하락 마감)
                AND spread(i) >= spread_ma(i) * climax_spread_ratio
                # 예: spread_ratio ≥ 1.5 → 평균 스프레드 1.5배 이상 = 공황적 가격 움직임
cond_sc_wick  : (bars[i].close - bars[i].low) / max(spread(i), 1e-9) >= sc_close_pct
                # 예: ≥ 0.30 → 바 저점 대비 종가가 스프레드의 30% 이상 위에 있어야 함
                # (하단 윅이 존재 = 매수 흡수의 증거)

SC 후보 발화 조건: cond_sc_vol AND cond_sc_price AND cond_sc_wick

# 복수 후보 중 가장 낮은 저점을 가진 바를 SC로 확정
SC = {bar_index, ts, price=bars[i].low, close=bars[i].close, volume=bars[i].volume}
TR.low_anchor = SC.price  # TR 하단 앵커
```

**참고**: PS(Preliminary Support)는 SC 전 첫 번째 의미있는 지지 반등으로, `vol_zscore >= ps_volume_zscore(기본 1.2)`이고 이전 하락 스윙에서 종가 반등이 발생한 바를 탐지한다. PS는 TR 경계 설정에 직접 사용되지 않으나 이벤트 목록에 기록된다.

**Buying Climax (BC) 탐지 조건 (분산 TR):**
```
cond_bc_vol   : vol_zscore(i, bars, climax_vol_lookback) >= climax_volume_zscore
cond_bc_price : bars[i].close > bars[i-1].close  (상승 마감)
                AND spread(i) >= spread_ma(i) * climax_spread_ratio
cond_bc_wick  : (bars[i].high - bars[i].close) / max(spread(i), 1e-9) >= bc_close_pct
                # 예: ≥ 0.25 → 상단 윅 존재 = 매도 공급 압력의 증거

BC = {bar_index, ts, price=bars[i].high, close=bars[i].close, volume=bars[i].volume}
TR.high_anchor = BC.price  # TR 상단 앵커 (분산)
```

**PSY(Preliminary Supply)**: BC 전 첫 번째 의미있는 공급 반응. `vol_zscore >= ps_volume_zscore` + 종가 하락 바. 이벤트 목록에 기록.

---

### STEP 2 — AR(Automatic Rally / Automatic Reaction)으로 TR 반대 극단 설정

**목적**: SC(또는 BC) 이후 반발 이동이 TR의 반대 극단을 결정한다.

**AR 탐지 조건 (매집 — SC 이후 상방 반발):**
```
# SC 확정 이후 bars[sc_idx+1 ...]를 순회
탐색 범위: sc_idx+1 to sc_idx+ar_max_bars (기본 30바)

AR_peak_price = max(bars[j].high for j in [sc_idx+1 ... sc_idx+ar_max_bars])
AR_peak_idx   = argmax(위와 동일 범위)

# AR은 SC 저점 대비 ar_min_retracement_pct 이상 반등해야 유효
AR 유효 조건: (AR_peak_price - TR.low_anchor) / max(TR.low_anchor, 1e-9)
              >= ar_min_retracement_pct  (기본 0.05 = 5%)

AR = {bar_index=AR_peak_idx, ts, price=AR_peak_price}
TR.high_anchor = AR.price  # TR 상단 앵커 (매집)
TR.range       = TR.high_anchor - TR.low_anchor
```

**AR 탐지 조건 (분산 — BC 이후 하방 반발):**
```
AR_trough_price = min(bars[j].low for j in [bc_idx+1 ... bc_idx+ar_max_bars])
AR_trough_idx   = argmin(위와 동일 범위)

AR 유효 조건: (TR.high_anchor - AR_trough_price) / max(TR.high_anchor, 1e-9)
              >= ar_min_retracement_pct

AR = {bar_index=AR_trough_idx, ts, price=AR_trough_price}
TR.low_anchor = AR.price  # TR 하단 앵커 (분산)
TR.range      = TR.high_anchor - TR.low_anchor
```

**TR 최소 범위 가드**: `TR.range / TR.low_anchor < tr_min_range_pct (기본 0.02 = 2%)`이면 TR 후보 폐기.

---

### STEP 3 — ST(Secondary Test) 식별

**목적**: SC/BC 이후 TR 경계를 재테스트하여 공급/수요가 실질적으로 감소했는지 확인한다. 미래 편향 방지: ST는 AR 확정 이후에만 탐지 시작.

**ST 탐지 조건 (매집 — SC 영역 재테스트):**
```
# ST 후보: SC 이후이면서 AR 이전 또는 이후, TR.low_anchor 근방 바
탐색 범위: ar_idx+1 이후 전체 (Phase B 내)
근방 정의: |bars[j].low - TR.low_anchor| / TR.range <= st_proximity_pct (기본 0.15)
            → TR 하단 경계의 ±15% 이내

ST 유효 조건:
  cond_st_vol   : bars[j].volume < SC.volume * st_volume_ratio (기본 0.70)
                  # ST 거래량은 SC의 70% 미만이어야 함 (공급 흡수 증거)
  cond_st_price : bars[j].low > TR.low_anchor (SC 저점 하향 돌파 없음)
                  → 단, Spring 탐지 전까지는 TR.low_anchor 소폭 침범(spring_break_pct 이내)
                    도 ST로 분류 가능
  cond_st_spread: spread(j) < spread(SC.bar_index) * st_spread_ratio (기본 0.80)
                  # 스프레드 축소 = 공황적 매도 진정

ST_list에 추가: {bar_index=j, ts, price=bars[j].low, volume=bars[j].volume}
```

**ST 탐지 조건 (분산 — BC 영역 재테스트):**
```
# 분산 ST: BC 고점 영역 재테스트
근방 정의: |bars[j].high - TR.high_anchor| / TR.range <= st_proximity_pct
ST 유효 조건:
  cond_st_vol   : bars[j].volume < BC.volume * st_volume_ratio
  cond_st_price : bars[j].high < TR.high_anchor (BC 고점 상향 돌파 없음)
  cond_st_spread: spread(j) < spread(BC.bar_index) * st_spread_ratio
```

---

### STEP 4 — Spring (매집) 및 UTAD (분산) 식별 — TR 극단 윅 돌파 후 Reclaim

Spring과 UTAD는 TR 극단의 false breakdown/breakout으로, 약한 보유자를 청산시킨 후 CO가 반대 방향으로 가격을 이끄는 핵심 트리거 이벤트다.

**Spring 탐지 조건 (매집 — TR 하단 돌파 후 회복):**
```
# TR.low_anchor 설정 이후 (AR 확정 후), TR 내 Phase B 이후 탐색

# 조건 1: 바의 저점이 TR 하단 경계 아래로 침범
cond_spr_break : bars[i].low < TR.low_anchor
                 AND (TR.low_anchor - bars[i].low) / TR.range <= spring_break_pct
                 # spring_break_pct 기본 0.05 = TR 범위의 5% 이내 침범
                 # 침범이 5% 초과 → Spring이 아닌 단순 하락 붕괴로 간주 (가드)
                 # 10% 초과 → 연속 저점 거리가 너무 크면 스탑 배치 불가 → 무효화

# 조건 2: 동일 바 또는 이후 spring_reject_bars 바 이내에 TR.low_anchor 위로 종가 회복
cond_spr_reclaim: (현재 바 종가 > TR.low_anchor)
                  OR (향후 spring_reject_bars 바 이내 어떤 바의 종가 > TR.low_anchor)
                  # spring_reject_bars 기본값 3
                  # 단, spring_reject_bars 조회는 이미 확정된 바만 사용

# 조건 3: 거래량 분류 (Spring Type 1/2/3)
spring_vol_z = vol_zscore(i, bars, climax_vol_lookback)

Spring Type 1 (강도 높은 흔들기):
  spring_vol_z >= climax_volume_zscore (기본 2.0)
  → 대량 매도 = 마지막 공급 공황. 이후 Test 필요

Spring Type 2 (중간):
  climax_volume_zscore * 0.4 <= spring_vol_z < climax_volume_zscore
  → 일부 증가. Secondary Test로 공급 소멸 확인 필수

Spring Type 3 (이상적 — 소량 흔들기):
  spring_vol_z < climax_volume_zscore * 0.4
  AND spread(i) < spread_ma(i) * 1.0  (좁은 스프레드)
  → 거의 공급 없음. 가장 신뢰할 수 있는 Spring

# 조건 4: 이전 Phase B 중 ST가 최소 1개 이상 확인됨
cond_spr_phase : len(ST_list) >= 1

Spring = {
  bar_index: i,
  ts: bars[i].ts,
  price: bars[i].low,           # 최저 침범 가격
  close: bars[i].close,
  volume: bars[i].volume,
  spring_type: 1 | 2 | 3,
  tr_break_pct: (TR.low_anchor - bars[i].low) / TR.range
}
발화 조건: cond_spr_break AND cond_spr_reclaim AND cond_spr_phase
```

**Test After Spring 탐지 조건:**
```
# Spring 확정 후 spring_reject_bars+1 부터 탐색
# Test = Spring 이후 TR.low_anchor 근방 재방문 (공급 재확인)
근방 정의: bars[j].low <= TR.low_anchor * (1.0 + st_proximity_pct)
           AND bars[j].low > TR.low_anchor * (1.0 - spring_break_pct * 0.5)
           # 스프링 저점보다는 높아야 함

Test 유효 조건:
  cond_test_vol   : bars[j].volume < Spring.volume * test_volume_ratio (기본 0.60)
                    # Test 거래량은 Spring의 60% 미만 = 공급 고갈 확인
  cond_test_close : bars[j].close > TR.low_anchor  # 종가는 TR 내부로 복귀

Test_of_Spring = {bar_index=j, ts, price=bars[j].low, volume=bars[j].volume}
```

**UTAD(Upthrust After Distribution) 탐지 조건 (분산 — TR 상단 돌파 후 반락):**
```
# TR.high_anchor 설정 이후, Phase B+에서 탐색
cond_utad_break  : bars[i].high > TR.high_anchor
                   AND (bars[i].high - TR.high_anchor) / TR.range <= utad_break_pct
                   # utad_break_pct 기본 0.05

cond_utad_reclaim: (현재 바 종가 < TR.high_anchor)
                   OR (향후 spring_reject_bars 바 이내 어떤 바의 종가 < TR.high_anchor)

cond_utad_vol    : vol_zscore(i, bars, climax_vol_lookback) 값으로 UTAD 타입 분류
                   (Spring과 동일 로직, 방향 반전)
                   # UTAD 후 거래량이 증가하면서 스프레드 축소 = 공급이 흡수를 압도

UTAD = {bar_index: i, ts, price: bars[i].high, close: bars[i].close, ...}
발화 조건: cond_utad_break AND cond_utad_reclaim AND len(ST_list) >= 1
```

**UT(Upthrust, 분산 Phase B)**: UTAD와 동일 구조이나 Phase B(BC 직후 초기 단계)에서 발생. 이벤트 목록에 별도 기록.

---

### STEP 5 — SOS(Sign of Strength) / SOW(Sign of Weakness) 식별

**목적**: Spring/Test 이후(매집) 또는 UTAD 이후(분산) 방향성 전환이 실제로 진행 중임을 확인하는 추진 이동.

**SOS 탐지 조건 (매집 — 상방 추진):**
```
# Spring 또는 Test_of_Spring 확정 이후 탐색

# 조건 1: 가격이 AR 고점 또는 TR.high_anchor를 향한 진행
# 상승 진행 측정: 기준 = Spring/Test 이후 최저 종가 대비 현재 바 종가의 상승폭
swing_up_pct = (bars[i].close - last_trough_close) / max(last_trough_close, 1e-9)
cond_sos_move : swing_up_pct >= sos_min_move_pct (기본 0.03 = 3%)

# 조건 2: 거래량 확대 (상승 시 볼륨 증가 = 노력-결과 일치)
cond_sos_vol  : bars[i].volume >= vol_ma(i) * sos_volume_ratio (기본 1.2)
                AND bars[i].volume > bars[i-1].volume  # 직전 바 대비 거래량 증가

# 조건 3: 스프레드 확대
cond_sos_sprd : spread(i) >= spread_ma(i) * 1.1  # 평균 스프레드 10% 이상 확대

# 조건 4: 상방 종가 (강세 바)
cond_sos_close: bars[i].close >= bars[i].open  # 양봉 또는 중립봉

SOS = {bar_index=i, ts, price=bars[i].close, volume=bars[i].volume,
       swing_up_pct=swing_up_pct}
발화 조건: cond_sos_move AND cond_sos_vol AND cond_sos_sprd AND cond_sos_close

# Effort vs. Result 발산 가드:
# 거래량은 sos_volume_ratio 이상이지만 종가 상승이 sos_min_move_pct 절반 미만이면
# → SOS가 아닌 "흡수(Absorption)" 패턴으로 기록 — 공급이 수요를 흡수하는 신호
```

**SOW 탐지 조건 (분산 — 하방 추진):**
```
# UTAD 또는 UT 확정 이후 탐색
swing_down_pct = (last_peak_close - bars[i].close) / max(last_peak_close, 1e-9)
cond_sow_move : swing_down_pct >= sos_min_move_pct
cond_sow_vol  : bars[i].volume >= vol_ma(i) * sos_volume_ratio
cond_sow_sprd : spread(i) >= spread_ma(i) * 1.1
cond_sow_close: bars[i].close <= bars[i].open  # 음봉 또는 중립봉

SOW = {bar_index=i, ts, price=bars[i].close, ...}
발화 조건: cond_sow_move AND cond_sow_vol AND cond_sow_sprd AND cond_sow_close
```

**Creek 및 JAC (Jump Across the Creek) — 매집 전용:**
```
# Creek = TR 내 단기 고점들을 연결한 저항선 (AR 고점과 이후 ST 반등 고점들)
creek_resistance = max(AR.price, max(st.price for st in ST_list if hasattr(st,'high')))
                  # 단순화: TR 상단 영역 = AR.price를 기준으로 사용

# JAC: SOS 이동이 creek_resistance를 종가 돌파할 때
JAC_cond: bars[i].close > creek_resistance * (1 + jac_buffer_pct)
          # jac_buffer_pct 기본 0.003 = 0.3% 확인 버퍼
          AND bars[i].volume >= vol_ma(i) * jac_volume_ratio (기본 1.5)
→ JAC = {bar_index=i, ts, price=bars[i].close}

# BUEC (Backup to Edge of Creek): JAC 이후 creek_resistance로 되돌아오는 테스트
BUEC_cond: JAC 확정 이후, bars[j].low <= creek_resistance * (1 + buec_tolerance_pct)
           # buec_tolerance_pct 기본 0.01 = 1%
           AND bars[j].volume < vol_ma(j) * 0.8  # 낮은 거래량 = 공급 없음
           AND bars[j].close > creek_resistance * (1 - buec_tolerance_pct)
→ BUEC = {bar_index=j, ts, price=bars[j].low}  # LPS의 한 형태
```

---

### STEP 6 — LPS(Last Point of Support) / LPSY(Last Point of Supply) 식별

**LPS 탐지 조건 (매집 Phase D — SOS 이후 얕은 되돌림):**
```
# SOS 또는 JAC 확정 이후, 되돌림(pullback) 구간에서 탐색

# 조건 1: 가격이 SOS 출발점 대비 lps_retracement_max 이내로 되돌림
# (직전 SOS 추진 저점 대비 피보나치 50% 이내 되돌림)
sos_thrust_range = SOS.price - Spring.price  (또는 Test_of_Spring.price)
max_retracement  = SOS.price - sos_thrust_range * lps_retracement_max (기본 0.50)
cond_lps_price  : bars[i].low >= max_retracement  # 50% 이상 되돌리지 않음
                  AND bars[i].low > TR.low_anchor * (1 + lps_above_tr_pct) (기본 0.01)
                  # LPS는 TR 하단보다 높아야 함 (Higher Low 구조)

# 조건 2: 낮은 거래량 (공급 감소 확인)
cond_lps_vol : bars[i].volume < vol_ma(i) * lps_volume_ratio (기본 0.80)
               AND bars[i].volume < SOS.volume * 0.70  # SOS 거래량의 70% 미만

# 조건 3: 좁은 스프레드 (압박 없음)
cond_lps_sprd: spread(i) < spread_ma(i) * 0.90

LPS = {bar_index=i, ts, price=bars[i].low, volume=bars[i].volume}
발화 조건: cond_lps_price AND cond_lps_vol AND cond_lps_sprd
```

**LPSY 탐지 조건 (분산 Phase D — SOW 이후 약한 반등):**
```
# SOW 확정 이후 반등 구간에서 탐색
sow_thrust_range   = BC.price - SOW.price  (또는 UTAD.price - SOW.price)
max_rally_target   = SOW.price + sow_thrust_range * lpsy_max_rally (기본 0.50)
cond_lpsy_price  : bars[i].high <= max_rally_target  # 50% 이상 회복하지 못함
                   AND bars[i].high < TR.high_anchor * (1 - lpsy_below_tr_pct) (기본 0.01)
                   # LPSY는 TR 상단보다 낮아야 함 (Lower High 구조)
cond_lpsy_vol    : bars[i].volume < vol_ma(i) * lps_volume_ratio  # 약한 수요
cond_lpsy_sprd   : spread(i) < spread_ma(i) * 0.90

LPSY = {bar_index=i, ts, price=bars[i].high, volume=bars[i].volume}
발화 조건: cond_lpsy_price AND cond_lpsy_vol AND cond_lpsy_sprd
```

---

### STEP 7 — 현재 페이즈(Phase A~E) 분류

SC/BC가 확정된 이후 탐지된 이벤트를 순서대로 평가하여 현재 페이즈를 결정한다. 모든 판정은 최근 확정 이벤트 기준으로 수행된다.

```
사용 전제:
  has_SC_or_BC       = SC 또는 BC가 탐지되었는가
  has_AR             = AR이 탐지되었는가
  has_ST             = ST_list 길이 >= 1
  has_spring_or_utad = Spring 또는 UTAD가 탐지되었는가
  has_test           = Test_of_Spring 또는 Test_of_UTAD 탐지 여부
  has_SOS_or_SOW     = SOS 또는 SOW가 탐지되었는가
  has_LPS_or_LPSY    = LPS 또는 LPSY가 탐지되었는가
  has_JAC            = JAC 탐지 여부 (매집) 또는 SOW_breakout (분산)

페이즈 분류 로직 (순서대로 평가, 맨 처음 매칭 조건 사용):

if NOT has_SC_or_BC:
    phase = None  # 스키매틱 미확립 → 0 반환

elif has_SC_or_BC AND NOT has_AR:
    phase = 'A'   # SC/BC 발생, AR 미확정

elif has_AR AND NOT has_ST:
    phase = 'A'   # AR까지 완료 (Phase A = PS/SC/AR까지)

elif has_ST AND NOT has_spring_or_utad:
    phase = 'B'   # ST 이후, Spring/UTAD 미발생 = Building Cause

elif has_spring_or_utad AND NOT has_SOS_or_SOW:
    phase = 'C'   # Spring/UTAD 발생, 첫 SOS/SOW 미확정

elif has_SOS_or_SOW AND NOT has_LPS_or_LPSY:
    phase = 'D'   # SOS/SOW 확정, LPS/LPSY 미확정

elif has_LPS_or_LPSY AND NOT has_JAC:
    phase = 'D'   # LPS/LPSY 확정, 아직 TR 내 (Phase D 중후반)

elif has_JAC:
    phase = 'E'   # JAC 또는 SOW 하방 돌파 이후 = 추세 시작

# 분산 Phase E 판정: SOW가 TR 하단(TR.low_anchor)을 종가 이탈했을 때
if schematic_type == 'distribution':
    if bars[current].close < TR.low_anchor * (1 - phase_e_breakout_pct):
        phase = 'E'
```

---

### STEP 8 — phase_confidence 점수화

각 이벤트의 품질과 수를 가중 합산하여 0~1 사이의 신뢰 점수를 산출한다.

```python
def score_phase_confidence(events: dict, schematic_type: str) -> float:
    score = 0.0

    # 이벤트별 기여 점수 (각 항목은 해당 이벤트가 탐지되었을 때만 가산)
    contributions = {
        # 매집
        'SC'             : 0.12,
        'AR'             : 0.08,
        'ST'             : 0.06,   # ST 1개당 0.06, 최대 0.12 (2개)
        'Spring'         : 0.15,   # Spring Type 3 = +0.05 보너스
        'Test_of_Spring' : 0.10,
        'SOS'            : 0.12,
        'LPS'            : 0.10,
        'JAC'            : 0.08,
        'BUEC'           : 0.07,
        # 분산
        'BC'             : 0.12,
        'PSY'            : 0.05,
        'UT'             : 0.07,
        'UTAD'           : 0.15,   # UTAD = Spring 등가
        'SOW'            : 0.12,
        'LPSY'           : 0.10,
    }

    for event_name, weight in contributions.items():
        if events.get(event_name):
            if event_name == 'ST':
                score += min(weight * len(events['ST_list']), weight * 2)
            elif event_name == 'Spring' and events.get('spring_type') == 3:
                score += weight + 0.05   # Type 3 보너스
            else:
                score += weight

    # 볼륨 일관성 보너스: TR 내 상승 이동의 평균 볼륨 > 하락 이동의 평균 볼륨이면
    # (매집) 또는 하락 이동 볼륨 > 상승 이동 볼륨이면 (분산) +0.07
    if events.get('volume_asymmetry_correct'):
        score += 0.07

    # open_interest 보조 확인 (크립토 한정)
    # Spring/Test 구간에서 OI가 감소했다가 SOS 후 증가하면 매집 확인 강화 +0.05
    if events.get('oi_confirmation'):
        score += 0.05

    return min(score, 1.0)
```

**볼륨 비대칭(volume_asymmetry) 계산:**
```
# TR 내 모든 상승 바(close >= open)의 평균 볼륨 vs 하락 바(close < open)의 평균 볼륨
# 매집: advance_vol_mean / decline_vol_mean >= vol_asymmetry_ratio (기본 1.1)
# 분산: decline_vol_mean / advance_vol_mean >= vol_asymmetry_ratio

up_bars   = [b for b in tr_bars if b.close >= b.open]
down_bars = [b for b in tr_bars if b.close < b.open]
if up_bars and down_bars:
    adv_vol = statistics.mean(b.volume for b in up_bars)
    dec_vol = statistics.mean(b.volume for b in down_bars)
    if schematic_type == 'accumulation':
        volume_asymmetry_correct = (adv_vol / max(dec_vol, 1e-9)) >= vol_asymmetry_ratio
    else:
        volume_asymmetry_correct = (dec_vol / max(adv_vol, 1e-9)) >= vol_asymmetry_ratio
```

---

**파라미터** —

| 파라미터 | 기본값 | 의미 |
|---------|--------|------|
| `climax_volume_zscore` | `2.0` | SC/BC/Spring 클라이맥스 바 볼륨의 최소 Z-스코어. 롤링 윈도우 내 평균 대비 2σ. 크립토처럼 변동성이 큰 시장은 2.5 권장. |
| `climax_vol_lookback` | `50` | 볼륨 Z-스코어 계산 롤링 윈도우 (바 수). |
| `climax_spread_ratio` | `1.5` | 클라이맥스 바의 최소 스프레드 배수 (평균 스프레드 대비). |
| `sc_close_pct` | `0.30` | SC 바에서 종가가 바 저점 기준 스프레드의 최소 비율 (흡수 증거). |
| `bc_close_pct` | `0.25` | BC 바에서 종가가 바 고점 기준 스프레드의 최소 하방 윅 비율. |
| `tr_lookback` | `200` | TR 후보 탐색 최대 바 범위. |
| `tr_min_range_pct` | `0.02` | TR 최소 범위 (TR 하단 대비 2%). 미달 시 TR 폐기. |
| `ar_max_bars` | `30` | AR 탐지 최대 허용 바 수 (SC/BC 이후). |
| `ar_min_retracement_pct` | `0.05` | AR이 유효하려면 SC 저점 대비 최소 5% 이상 반등해야 함. |
| `st_proximity_pct` | `0.15` | ST 후보 바의 저점/고점이 TR 경계 대비 TR 범위의 15% 이내이어야 함. |
| `st_volume_ratio` | `0.70` | ST 볼륨은 SC/BC 볼륨의 최대 70%. |
| `st_spread_ratio` | `0.80` | ST 스프레드는 SC/BC 스프레드의 최대 80%. |
| `spring_break_pct` | `0.05` | Spring의 TR 하단 침범 허용 상한 (TR 범위 대비 5%). 초과 시 단순 하락으로 간주. |
| `spring_reject_bars` | `3` | Spring 침범 후 TR 상단 회복에 허용되는 최대 바 수. |
| `test_volume_ratio` | `0.60` | Test 볼륨은 Spring 볼륨의 최대 60%. |
| `utad_break_pct` | `0.05` | UTAD의 TR 상단 침범 허용 상한 (TR 범위 대비 5%). |
| `sos_min_move_pct` | `0.03` | SOS/SOW 이동의 최소 종가 변화율 (3%). |
| `sos_volume_ratio` | `1.20` | SOS/SOW 바의 최소 볼륨 배수 (vol_ma 대비). |
| `lps_retracement_max` | `0.50` | LPS/LPSY가 SOS/SOW 추진 범위의 최대 되돌림 비율 (50%). |
| `lps_volume_ratio` | `0.80` | LPS/LPSY 바의 최대 볼륨 배수 (vol_ma 대비 80% 이하). |
| `lps_above_tr_pct` | `0.01` | LPS 저점이 TR 하단 대비 최소 1% 이상 높아야 함 (Higher Low). |
| `jac_buffer_pct` | `0.003` | JAC 돌파 확인 버퍼 (0.3%). |
| `jac_volume_ratio` | `1.50` | JAC 바의 최소 볼륨 배수 (vol_ma 대비). |
| `buec_tolerance_pct` | `0.01` | BUEC가 Creek 경계에 근접하다고 판단하는 허용 범위 (1%). |
| `phase_e_breakout_pct` | `0.01` | 분산 Phase E 판정을 위한 TR 하단 종가 이탈 최소 비율 (1%). |
| `vol_asymmetry_ratio` | `1.10` | TR 내 볼륨 비대칭 인정 최소 배수. |
| `ps_volume_zscore` | `1.20` | PS/PSY 볼륨 Z-스코어 최소값. |
| `vol_ma_period` | `20` | vol_ma 계산 기본 기간 (바 수). |
| `spread_ma_period` | `20` | spread_ma 계산 기본 기간 (바 수). |
| `pivot_lookback` | `3` | 스윙 피벗 확정 좌/우 룩백 바 수. |

---

**출력 필드** —

```python
WyckoffResult = {
    # 스키매틱 메타데이터
    'schematic': {
        'type': 'accumulation' | 'distribution' | None,
        'tr_high': float,              # TR 상단 가격 (AR 고점 또는 BC 고점)
        'tr_low': float,               # TR 하단 가격 (SC 저점 또는 AR 저점)
        'tr_range': float,             # tr_high - tr_low
        'events': [                    # 시간순 탐지된 이벤트 목록
            {
                'name': str,           # 'PS'|'SC'|'AR'|'ST'|'Spring'|'Test'|
                                       # 'SOS'|'LPS'|'JAC'|'BUEC'|
                                       # 'PSY'|'BC'|'UT'|'UTAD'|'SOW'|'LPSY'
                'ts': datetime,        # 이벤트 발생 바의 타임스탬프
                'price': float,        # 이벤트 특성 가격 (SC=저점, AR=고점, SOS=종가 등)
                'volume': float,       # 해당 바 볼륨
                'bar_index': int,      # list[PriceBar] 내 인덱스
                'detail': dict,        # 이벤트별 추가 정보 (spring_type, vol_zscore 등)
            }
        ],
    },
    # 현재 페이즈
    'phase': 'A' | 'B' | 'C' | 'D' | 'E' | None,
    # 페이즈 신뢰 점수
    'phase_confidence': float,         # 0.0 ~ 1.0
    # 진입 신호
    'entry_signal': {
        'signal': 'long' | 'short' | 'wait' | 'avoid',
        'trigger': str,                # 신호 발생 이벤트 이름 (예: 'LPS', 'BUEC')
        'ts': datetime,
        'price': float,                # 권장 진입 가격 기준선
        'stop_price': float,           # 스탑 기준 (Spring 저점 - 1 ATR 등)
        'target_price': float,         # 1차 목표가 (TR 상단 기준 또는 측정이동)
        'reason': str,                 # 인간 가독 설명
    },
    # 볼륨 비대칭 여부
    'volume_asymmetry_correct': bool,
    # OI 확인 (크립토 한정, 제공된 경우만)
    'oi_confirmation': bool,
}
```

---

**진입 관련성** —

와이코프 스키매틱 신호는 페이즈와 이벤트 조합에 따라 다음과 같이 진입 결정을 안내한다.

| 상태 | 신호 | 근거 |
|------|------|------|
| Phase A (매집 SC+AR만 확인됨) | `wait` | TR이 막 형성 중. 방향 불확실. |
| Phase B (ST 탐지 중) | `wait` | CO가 포지션 구축 중. 돌발 흔들기 가능. |
| Phase C — Spring Type 3 탐지 직후 | `long` 준비 | 공급 고갈 최고 신호. Test 바를 기다린 후 진입. |
| Phase C — Spring 후 Test 확정 | `long` 진입 | 진입가 = Test 종가, 스탑 = Spring 저점 아래 0.5~1 ATR |
| Phase D — SOS 확정 | `long` | 방향성 확인. 진입 또는 피라미딩 가능. |
| Phase D — LPS / BUEC 확정 | `long` 추가 진입 | 최적 진입 구간. 스탑 = LPS 저점 아래 |
| Phase E (JAC 이후) | `long` 유지 / 신규 진입 회피 | 추세 진행 중. 재진입은 새 TR(재매집) 형성 후 |
| Phase A (분산 BC+AR만 확인됨) | `wait` | 상단 TR 형성 중. 숏 진입 시기상조. |
| Phase B (분산 ST/UT 탐지 중) | `wait` | CO가 분산 중. 방향 확인 불충분. |
| Phase C — UTAD 탐지 직후 | `short` 준비 | 마지막 강세 함정. Test 대기. |
| Phase D — SOW 확정 | `short` | 방향성 확인. 스탑 = UTAD 고점 위 |
| Phase D — LPSY 확정 | `short` 추가 진입 | 수요 소진 확인. 최적 숏 진입. |
| Phase E (분산, TR 하단 이탈) | `short` 유지 | 하락 추세 시작. |
| `phase_confidence < 0.35` 또는 `phase = None` | `avoid` | 스키매틱 미확립. 진입 금지. |

**스탑 배치 원칙**: 매집 롱 진입 시 스탑은 Spring 저점 또는 LPS 저점 아래 `0.5 × ATR(14)`. 분산 숏 진입 시 스탑은 UTAD 고점 또는 LPSY 고점 위 `0.5 × ATR(14)`.

**목표가 산출 (측정이동, Measured Move)**: 매집 1차 목표 = `TR.high_anchor + TR.range`. 분산 1차 목표 = `TR.low_anchor - TR.range`.

---

**컨플루언스** —

| 조건 | 방향 | 가중치 |
|------|------|--------|
| Phase D/E + `phase_confidence >= 0.60` | 스키매틱 방향 | 0.75 |
| Phase D + `phase_confidence 0.40~0.59` | 스키매틱 방향 | 0.45 |
| Phase C Spring Type 3 + Test 확인 | 상승(매집) | 0.50 |
| Phase C UTAD + volume_asymmetry_correct | 하락(분산) | 0.50 |
| Phase A 또는 B | 중립 | 0.00 |
| `phase = None` 또는 `phase_confidence < 0.35` | 없음 | 0.00 |
| SOS + LPS 순서 연속 (매집 D) | 상승 보너스 | +0.10 |
| SOW + LPSY 순서 연속 (분산 D) | 하락 보너스 | +0.10 |
| JAC + BUEC (매집 E 초입) | 상승 | 0.70 |
| OI 확인 있음 (크립토, oi_confirmation=True) | 스키매틱 방향 | +0.05 |

---

**거짓신호 가드** —

1. **클라이맥스 없이 TR 가정 금지**: SC 또는 BC가 탐지되지 않은 상태에서 AR이나 ST를 식별하거나 페이즈를 판정하지 않는다. `has_SC_or_BC = False`이면 전체 스키매틱을 즉시 `None` 반환.

2. **Spring vs. 단순 하락 구분**: `(TR.low_anchor - bars[i].low) / TR.range > spring_break_pct (5%)`이면 Spring이 아니라 TR 붕괴(Breakdown)로 간주한다. 이 경우 매집 스키매틱을 폐기하고 분산 또는 새로운 TR 형성 가능성을 탐색한다.

3. **Spring 이후 Reclaim 실패**: `spring_reject_bars` 이내에 종가가 TR.low_anchor 위로 회복되지 않으면 Spring 후보를 확정하지 않는다 — 이는 false spring이 아니라 실제 붕괴다.

4. **ST 볼륨 증가 경고**: ST 바의 볼륨이 SC 볼륨 이상이면 공급이 완전히 흡수되지 않은 신호다. ST를 이벤트 목록에 기록하되 Phase B 진입을 보류하고 `phase_confidence`를 0.10 감산한다.

5. **SOS 없는 단독 LPS 무효**: SOS 탐지 없이 LPS만 탐지되면 해당 바는 LPS가 아닌 단순 지지 반응(ST 변형)으로 재분류한다.

6. **페이즈 미식별 시 0 반환**: `phase = None` 또는 필수 이벤트(SC+AR 쌍 또는 BC+AR 쌍) 미확인 상태에서 진입 신호를 발행하지 않는다. `entry_signal.signal = 'avoid'`, `phase_confidence = 0.0`.

7. **TR 범위 너무 좁음**: `TR.range / TR.low_anchor < tr_min_range_pct`이면 TR이 아닌 노이즈로 판정, 스키매틱 폐기.

8. **볼륨 데이터 부재 처리**: `bars[i].volume == 0` 또는 `None`인 바가 전체 TR 바의 20% 이상이면 볼륨 조건을 건너뛰고 가격 조건만으로 이벤트를 탐지하되 `phase_confidence`를 최대 0.50으로 캡핑한다.

9. **OI 역방향 경고 (크립토)**: Spring 구간에서 `open_interest`가 증가하면(롱 포지션 신규 진입 증가) 매집 확인이 약화된다. 이 경우 `oi_confirmation = False`로 설정하고 Spring 신뢰 가중치를 절반으로 감산.

---

**함정** —

1. **스키매틱은 사후에만 명확하다**: 실시간 처리에서 Phase A의 SC가 "최저점"인지는 이후 AR과 ST가 확정된 후에야 알 수 있다. 탐지 직후 `phase_confidence`가 낮은 이유가 여기에 있다. 낮은 신뢰 점수에서의 조기 진입은 와이코프의 원칙과 상충한다.

2. **모든 횡보 레인지가 매집이 아니다**: 선행 추세가 없거나, SC의 볼륨 클라이맥스가 발생하지 않았거나, 단순히 방향을 잃은 횡보 구간도 TR처럼 보일 수 있다. 반드시 선행 추세(최소 `tr_lookback` 바 이내의 추세 이동 확인) + 클라이맥스 이벤트 쌍(SC+AR 또는 BC+AR)을 요구한다.

3. **Effort-Result 해석의 주관성**: "대량거래에도 가격이 거의 안 움직임"이 흡수(매집 긍정)인지 공급 과잉(분산 부정)인지는 방향과 컨텍스트에 따라 반대로 해석된다. 알고리즘은 스프레드와 종가 위치를 추가하여 모호성을 줄이지만 완전히 제거하지는 못한다.

4. **Spring은 선택적이다**: 일부 매집 구조에서는 명확한 Spring 없이 Phase B에서 바로 SOS로 전환된다. 알고리즘이 `has_spring = False` 상태에서 Phase D를 탐지하면 `phase_confidence`를 0.15 감산하여 불완전한 스키매틱임을 표시한다.

5. **다중 스키매틱 중첩**: Phase E 이후 새로운 재매집(Re-accumulation) TR이 형성될 수 있다. `detect_wyckoff_schematic`은 가장 최근의 완전한 TR만 반환한다. 중첩 탐지가 필요하면 `tr_lookback`을 줄이고 함수를 다시 호출한다.

6. **타임프레임 의존성**: 일봉 기준 Phase B는 수개월에 걸쳐 진행될 수 있다. 동일한 파라미터를 5분봉에 적용하면 노이즈로 인한 오탐이 급증한다. 타임프레임별 파라미터 조정(`climax_vol_lookback`, `spring_break_pct`, `pivot_lookback`)이 필수다.

7. **크립토 Funding Rate 왜곡**: 영구선물 시장에서 극단적인 Funding Rate 구간에서는 볼륨이 청산(Liquidation)에 의해 인위적으로 증폭된다. 이런 볼륨을 SC로 오탐할 수 있다. `open_interest`가 동시에 급감하지 않는다면 진정한 클라이맥스 흡수가 아닐 수 있다.

---

**참고** —

- [Wyckoff Glossary — Wyckoff Stock Market Institute](https://wyckoffsmi.com/wyckoff-glossery/)
- [The Wyckoff Method: A Tutorial — ChartSchool, StockCharts.com](https://chartschool.stockcharts.com/table-of-contents/market-analysis/wyckoff-analysis-articles/the-wyckoff-method-a-tutorial)
- [Wyckoff Method — Wyckoff Analytics](https://www.wyckoffanalytics.com/wyckoff-method/)
- [Identifying Wyckoff Springs with Algorithmic Trading — Wyckoff Analytics](https://www.wyckoffanalytics.com/identifying-wyckoff-springs-with-algorithmic-trading-strategies/)
- [Wyckoff Accumulation Pattern: Phases, Schematics & Trading Guide — TrendSpider](https://trendspider.com/learning-center/chart-patterns-wyckoff-accumulation/)
- [Wyckoff Accumulation Explained — StoicFX](https://stoicfx.com/learn/wyckoff-accumulation)
- [Decoding Wyckoff Schematics: The Ultimate Cheat Sheet — PriceActionNinja](https://priceactionninja.com/decoding-wyckoff-schematics-the-ultimate-cheat-sheet/)
- [The Ultimate Guide to Wyckoff Accumulation & Distribution — Phemex Academy](https://phemex.com/academy/wyckoff-accumulation)
- [Wyckoff Trading Method: Accumulation & Distribution — Market Bulls](https://market-bulls.com/wyckoff-trading-method-accumulation-distribution-schematics/)

## 8. 차트패턴 (chart_patterns)

**정의** — 차트패턴(Classical Chart Patterns)은 가격 피벗 시퀀스에서 반복적으로 나타나는 기하학적 구조물로, 트레이더 심리(분산, 축적, 추세 모멘텀)를 인코딩하며 추세선 기하학을 통해 방향성 예측 확률을 제공한다. 각 패턴은 최소한 확정된 스윙 피벗(로컬 극값, 좌우 N개 바가 완전히 닫혀야 유효) 시퀀스, 해당 피벗들을 잇는 추세선 피팅, 그리고 파생 넥라인 또는 경계선을 돌파하는 트리거 이벤트로 구성된다. 패턴은 반전형(Double Top/Bottom, Head-and-Shoulders, Rising/Falling Wedge)과 지속형(Flags, Pennants, Rectangles, Triangles, Cup-and-Handle)으로 분류된다. 패턴 기저부 높이(base height)는 돌파점에서 측정이동(measured move) 목표가 산출에 사용된다. 추세 컨텍스트(상승추세=HH+HL 시퀀스, 하락추세=LH+LL 시퀀스, 횡보=방향성 없음)는 어떤 패턴 계열이 예상되는지를 결정하고 각 형성의 강세/약세 사전 확률(prior)을 수정한다.

**탐지 알고리즘** —

1. **STEP 0 — 전제 조건**: 바를 ts 기준 오름차순 정렬, 0-기반 정수 인덱스 i 부여(0=가장 오래된 바). `bar.mid = (bar.high + bar.low) / 2` 정의. 모든 탐지는 `index <= current_bar_index`인 바만 사용한다. 인덱스 i의 피벗은 바 `i+swing_lookback`이 완전히 닫힌 후에만 확정(CONFIRMED)된다. 라이브 피드에서는 확정 우측 바가 모두 닫힌 후에만 피벗 탐지 결과를 발행한다. 이것이 핵심 미래 편향(lookahead bias) 가드다.

2. **STEP 1 — 스윙 피벗 탐지 (프랙탈 방법)**: 확정된 바 범위 `[swing_lookback, len(confirmed_bars)-swing_lookback-1]`에서, 각 바 i에 대해: `bars[i].high == max(bars[j].high for j in range(i-swing_lookback, i+swing_lookback+1))`이면 SwingHigh; `bars[i].low == min(bars[j].low for j in range(i-swing_lookback, i+swing_lookback+1))`이면 SwingLow. 피벗 목록을 `(i, price, direction)` 형태로 수집하고, 연속 동방향 피벗은 하나만 유지(연속 H면 높은 것, 연속 L면 낮은 것). `swing_lookback=2`이면 피벗이 현재가보다 2바 뒤처지고, 인트라데이에 권장되는 3~4 설정 시 지연이 그만큼 증가한다.

3. **STEP 2 — 추세 분류**: 가장 최근 확정 SwingHigh 2개(SH1 < SH2, 인덱스 기준)와 SwingLow 2개(SL1 < SL2)를 추출. 상승추세 = `SH2.price > SH1.price AND SL2.price > SL1.price`. 하락추세 = `SH2.price < SH1.price AND SL2.price < SL1.price`. 횡보/축적 = 그 외(상충 또는 플랫). `trend_context in {'uptrend','downtrend','range'}`로 저장.

4. **STEP 3 — 더블 탑(Double Top) 탐지**: 가장 최근 확정 SwingHigh 3개 `[H1, H2, H3]` 인덱스 오름차순(H1=가장 오래됨). H2=왼쪽 피크, H3=오른쪽 피크; H1은 선행 구조 확인용. 넥라인 = H2~H3 사이 가장 낮은 SwingLow(두 피크 사이 골). 수용 조건: `abs(H3.price - H2.price) / H2.price <= peak_tolerance(0.03)`; H2~H3 바 수 `>= min_peak_separation(5)`; H3 볼륨 < H2 볼륨(두 번째 피크 볼륨 감소 = 매수 압력 약화 확인); 패턴 스팬 `>= min_pattern_bars(10)`. 돌파: 현재 종가가 `neckline.price` 아래로 교차. 목표가 = `neckline.price - (H2.price - neckline.price)`.

5. **STEP 4 — 더블 바텀(Double Bottom) 탐지**: STEP 3의 거울. SwingLow 3개 `[L1, L2, L3]` 사용. 왼쪽 골 = L2, 오른쪽 골 = L3. 넥라인 = L2~L3 사이 가장 높은 SwingHigh(두 골 사이 피크). 수용 조건: `abs(L3.price - L2.price)/L2.price <= peak_tolerance(0.03)`; 바 간격 `>= min_peak_separation(5)`; L3 볼륨 >= L2 볼륨 선호(강하게 감소하지 않아야 함); 스팬 `>= min_pattern_bars(10)`. 돌파: 종가가 `neckline.price` 위로 교차. 목표가 = `neckline.price + (neckline.price - L2.price)`.

6. **STEP 5 — 헤드 앤 숄더(Head and Shoulders, 천장형) 탐지**: 마지막 5개 교번 확정 피벗을 High 시퀀스로 수집: `LS_high(i1), LS_low(i2), HEAD_high(i3), RS_low(i4), RS_high(i5)` (i1<i2<i3<i4<i5). 수용 조건: `HEAD_high.price > LS_high.price AND HEAD_high.price > RS_high.price`(헤드가 최고점); `abs(RS_high.price - LS_high.price)/LS_high.price <= shoulder_sym_pct(0.05)`; 시간 대칭 검사: `left_span = i3-i1`, `right_span = i5-i3`; `max(left_span, right_span) / min(left_span, right_span) > time_sym_ratio(2.5)`면 거부; 넥라인 = `(i2, LS_low.price)`와 `(i4, RS_low.price)`를 잇는 선; `neckline_slope = (RS_low.price - LS_low.price)/(i4 - i2)`; 상향 및 하향 경사 넥라인 모두 정식 유효하며, `neckline_slope < neckline_slope_limit(-0.005/bar)`(급격히 하향)인 경우만 거부. 돌파 = 종가가 현재 바 인덱스에서의 넥라인 값 아래로 교차. 목표가 = `neckline_at_breakout - (HEAD_high.price - neckline_at_head_index)`.

7. **STEP 6 — 역 헤드 앤 숄더(Inverse Head and Shoulders, 바닥형) 탐지**: STEP 5의 거울. Low 피벗 사용: `LS_low(i1), LS_high(i2), HEAD_low(i3), RS_high(i4), RS_low(i5)`. HEAD_low는 세 골 중 최저값이어야 함. 두 개 개입 고점 `(i2, i4)`를 잇는 넥라인. 동일한 `time_sym_ratio` 검사 적용. 돌파 = 종가가 넥라인 값 위로 교차. 목표가 = `neckline_at_breakout + (neckline_at_head_index - HEAD_low.price)`.

8. **STEP 7 — 추세선 피팅 헬퍼**: n개 `(index, price)` 피벗 목록을 최소제곱으로 기울기 m과 절편 b 계산: `sum_x = sum(x for x,y in pts)`; `sum_y = sum(y for x,y in pts)`; `sum_xy = sum(x*y for x,y in pts)`; `sum_x2 = sum(x*x for x,y in pts)`; `m = (n*sum_xy - sum_x*sum_y) / (n*sum_x2 - sum_x**2) if (n*sum_x2 - sum_x**2) != 0 else 0.0`; `b = (sum_y - m*sum_x) / n`. `r_squared`와 잔차 표준편차(`residual_std`)를 예측값 대 실제값으로 계산. 정확히 2점의 추세선은 정의상 `r_squared=1.0`이므로 품질 게이트로 사용하지 않는다. `trendline_fit_tolerance` 게이트(`residual_std / mean_price <= 0.01`)는 피벗 터치 3개 이상일 때만 적용한다.

9. **STEP 8 — 삼각형(Triangle) 탐지**: 마지막 `triangle_lookback_pivots(8)`개 교번 확정 피벗을 수집. `highs_pts = [(i, p) for direction=='H']`, `lows_pts = [(i, p) for direction=='L']`로 분리. 각 2개 이상 필요. 고점 추세선 피팅: `(m_top, b_top, r2_top)`, 저점 추세선 피팅: `(m_bot, b_bot, r2_bot)`. `apex_x = (b_bot - b_top)/(m_top - m_bot) if m_top != m_bot else float('inf')`. 삼각형 조건: `apex_x > current bar index`(선들이 앞에서 수렴). 돌파는 패턴 시작부터 apex까지 거리의 50%~75% 사이에서 발생해야 함(`breakout_pct_apex_min=0.50`, `breakout_pct_apex_max=0.75`). 분류: (1) 상승 삼각형(Ascending) = `m_top ≈ 0(|m_top|/(price_range/bar_range) < flat_slope_threshold=0.1) AND m_bot > 0` → 상단 수평 저항, 하단 상승 지지 → 강세 바이어스; (2) 하락 삼각형(Descending) = `m_bot ≈ 0 AND m_top < 0` → 하단 수평 지지, 상단 하락 저항 → 약세 바이어스; (3) 대칭 삼각형(Symmetrical) = `m_top < 0 AND m_bot > 0 AND abs(m_top/m_bot - 1.0) < 0.5` → 중립. 추세선당 2회 이상 터치 필요(r_squared 게이팅은 3회 이상). 기저 높이 = `highs_pts[0].price - lows_pts[0].price`. 돌파 = 종가가 상단 추세선값 초과(강세) 또는 하단 추세선값 미만(약세). 목표가 = `breakout_price ± base_height`.

10. **STEP 9 — 웨지(Wedge) 탐지**: STEP 8의 추세선 피팅을 그대로 사용하되, 두 기울기가 동일 부호이면서 수렴하는 경우만 해당. 상승 웨지(Rising Wedge): `m_top > 0 AND m_bot > 0 AND m_bot > m_top`(하단선이 더 가파르게 상승 → 채널이 위로 좁아짐) → 하방 돌파 예상(약세). 하락 웨지(Falling Wedge): `m_top < 0 AND m_bot < 0 AND m_top < m_bot`(상단선이 더 가파르게 하락, 즉 더 음수 → 채널이 아래로 좁아짐) → 상방 돌파 예상(강세). 조건: `apex_x > current bar` AND 각 추세선 3회 이상 터치 필요(r_squared 게이팅). 형성 중 어떤 종가도 채널을 `price_buffer(0.002 * current_price)` 초과해 벗어나면 즉시 무효화. 웨지 너비 = 패턴 시작(첫 피벗 바 인덱스)에서 두 추세선 간 수직 거리. 돌파 = 종가가 반대편 경계 밖으로 이동(상승 웨지는 하단 경계 아래, 하락 웨지는 상단 경계 위). 목표가 = `breakout_price ± wedge_width`.

11. **STEP 10 — 플래그 / 페넌트(Flag / Pennant) 탐지**: 먼저 폴(pole) 탐지: 종가 방향이 단조로운 연속 바 구간으로, 누적 범위 `>= pole_min_pct(0.04 = 4%)` AND `>= pole_min_bars(3)` 바; 폴은 가장 최근 극단 종가에서 끝남. 플래그 통합 구간 = 폴 종료 후 5~20바. 통합 구간의 고점과 저점에 대해 추세선 피팅. 플래그 조건: 상단/하단선이 대략 평행(`|m_top - m_bot| / (abs(max(m_top,m_bot)) + 1e-9) < 0.3`) AND 기울기가 폴 방향 반대(강세 폴 → 하향 플래그 채널: `m_top < 0 AND m_bot < 0`; 약세 폴 → 상향 플래그 채널: `m_top > 0 AND m_bot > 0`). 페넌트 조건: 선들이 수렴(앞에서 apex), 대략 대칭(`|m_top + m_bot| < 0.2 * (|m_top| + |m_bot| + 1e-9)`). 플래그 바디의 되돌림 `<= 50%` of pole range. 통합 바 수: `flag_min_bars(5)~flag_max_bars(20)`. 거래량: 통합 기간 동안 감소 필수(볼륨의 최소제곱 회귀 기울기 < 0). 돌파: 통합 경계 위/아래로 종가 교차 + 볼륨 `>= breakout_vol_ratio(1.5) * mean_volume(최근 20바)`. 목표가 = `breakout_price + pole_range`(폴 높이의 측정이동).

12. **STEP 11 — 직사각형 / 채널(Rectangle / Channel) 탐지**: 마지막 `channel_lookback_pivots(10)`개 교번 확정 피벗 수집. 고점 추세선과 저점 추세선 피팅. 직사각형/채널 조건: 두 기울기가 모두 평탄(`|m_top|/(price_range/bar_range) < flat_slope_threshold=0.1 AND |m_bot| 동일`). 각 경계 2회 이상 터치 필요. 패턴 스팬 `>= min_pattern_bars(10)`. 일봉 한정: 패턴 스팬 < 15바 AND 선행 방향성 폴이 명확하면 플래그로 재분류(StockCharts 규칙); 이 재분류는 일봉에만 적용하며 인트라데이 바에는 적용하지 않는다. 채널 높이 = `mean(highs_pts prices) - mean(lows_pts prices)`. 돌파: 종가 > `mean_top + 0.003*price(0.3% 확인 버퍼)` 또는 종가 < `mean_bot - 0.003*price`. 목표가 = `breakout_price ± channel_height`.

13. **STEP 12 — 컵 앤 핸들(Cup and Handle) 탐지**: `trend_context=='uptrend'` 필수. `cup_lookback_bars(60~252)`바 윈도우 내에서: (a) `left_rim_idx` = 패턴 시작 전 마지막 주요 SwingHigh 인덱스; (b) `cup_bottom_idx` = `left_rim_idx` 이후 가장 낮은 SwingLow; (c) `right_rim_idx` = `cup_bottom_idx` 이후 다음 SwingHigh로서 `right_rim.price >= left_rim.price * (1.0 - cup_rim_tolerance) AND <= left_rim.price * 1.03`; (d) `cup_depth = left_rim.price - cup_bottom.price`; `0.12 <= cup_depth/left_rim.price <= 0.33` 검증(O'Neil의 원본 IBD 기준: 12~33%; 주식에 적용, 크립토는 0.50까지 허용 고려); (e) 컵 형태가 U자형인지 검증(V자형 거부): 컵 바 중 `close < cup_bottom.price + 0.1*cup_depth`인 비율 `>= 0.30`(바닥 부근 30% 이상의 바가 바닥 영역에 머무는 것이 바닥 라운딩을 나타냄); (f) 핸들 = `right_rim_idx` 이후 통합 바: `handle_low = handle 구간의 최소 SwingLow`; `handle_depth = right_rim.price - handle_low.price`; 핸들 허용 조건: `handle_depth <= cup_depth * 0.33`(이상적: 컵 깊이의 1/3 이하; 최대 허용: 0.50 초과 시 패턴 무효) AND 핸들 바 수 `< cup_bars * 0.5`; 핸들 저점은 컵 상단 2/3 내에 위치해야 함(`handle_low.price > cup_bottom.price + cup_depth * 0.33`); (g) 돌파: `close > right_rim.price` AND 볼륨 `>= breakout_vol_ratio(1.4) * mean_volume(최근 10바)`. 목표가 = `right_rim.price + cup_depth`.

14. **STEP 13 — 완화(Mitigated) / 만료(Expired) 패턴 확인**: 형성 이후 다음 조건이면 `mitigated=True`: (a) 가격이 목표 구간을 채우도록 복귀; (b) 반전 패턴의 경우, 더블 탑이면 두 피크 레벨 위로, 더블 바텀이면 두 골 레벨 아래로 `peak_tolerance` 초과하여 종가 복귀. 삼각형/웨지의 경우: `apex_x <= current bar index`이면(선들이 교차되어 패턴 소멸) 만료. `ts_mitigated = bar.ts`(해당 시점).

15. **STEP 14 — 출력**: 각 탐지 패턴마다 딕셔너리 반환: `{pattern_type, direction('bullish'/'bearish'/'neutral'), ts_start, ts_end, ts_breakout(또는 None), zone_low, zone_high(패턴 피벗 경계 박스), neckline_price(해당 없는 패턴은 None), target_price, pivot_sequence(list of (ts, price, 'H'|'L')), strength(volume_ratio/trendline_r_squared/대칭성 점수로 구성된 복합 0~1), mitigated(bool), ts_mitigated(datetime 또는 None), trend_context('uptrend'/'downtrend'/'range'), trendline_r_squared_top(float 또는 None — 삼각형/웨지/플래그/직사각형에만; 더블탑/바텀 및 H&S에는 None), trendline_r_squared_bot(float 또는 None — 동일 범위), pole_range(float 또는 None — 플래그/페넌트에만), volume_ratio_at_breakout(float 또는 None — ts_breakout이 Not None일 때), apex_bar_index(int 또는 None — 삼각형/웨지/페넌트에만)}`.

**파라미터** —

| name | default | 의미 |
|------|---------|------|
| `swing_lookback` | `2` | 스윙 고/저점 확정에 필요한 좌우 바 수(프랙탈 룩백 N). 인덱스 i 피벗은 바 i+N 닫힘 후만 확정. 일봉에는 2, 인트라데이 노이즈가 많은 데이터는 3~4 권장. |
| `peak_tolerance` | `0.03` | 더블 탑/바텀의 두 피크/골 간 최대 허용 가격 차이 비율(3%). 고변동성(크립토) 환경에서는 고정 비율 대신 ATR(14)/price 스케일링 고려. |
| `min_peak_separation` | `5` | 더블 탑/바텀 두 피크 간 최소 바 수. |
| `min_pattern_bars` | `10` | 모든 패턴(플래그 제외)의 최소 총 바 스팬. |
| `shoulder_sym_pct` | `0.05` | H&S 좌우 어깨 간 최대 가격 높이 차 비율(5%). |
| `time_sym_ratio` | `2.5` | H&S 헤드 중심 기준 좌/우 스팬 비율의 최대값. 공식: `max(left_span, right_span) / min(left_span, right_span) > time_sym_ratio`이면 거부. 한쪽이 다른 쪽보다 2.5배 이상 길면 무효. |
| `flat_slope_threshold` | `0.1` | `price_range/bar_range` 대비 기울기 크기가 이 값 미만이면 '평탄(flat)'으로 분류(상승/하락 삼각형, 직사각형 판별에 사용). |
| `trendline_fit_tolerance` | `0.01` | 피벗 3개 이상인 추세선의 최대 `residual_std / mean_price`(1%). 2점 추세선에는 적용하지 않음. |
| `triangle_lookback_pivots` | `8` | 삼각형 추세선 피팅에 사용할 교번 피벗 수. |
| `breakout_pct_apex_min` | `0.50` | 삼각형/웨지/페넌트의 돌파가 패턴 시작~apex 거리 중 이 비율 이후에 발생해야 함(이상적 돌파 구간 50~75%). |
| `breakout_pct_apex_max` | `0.75` | 삼각형/웨지 돌파의 apex 거리 기준 최대 비율; 이후 돌파는 소멸로 간주. |
| `pole_min_pct` | `0.04` | 유효한 플래그폴의 최소 누적 가격 이동(가격 대비 4%). |
| `pole_min_bars` | `3` | 플래그폴 형성에 필요한 최소 바 수. |
| `flag_min_bars` | `5` | 플래그/페넌트 바디의 최소 통합 바 수. |
| `flag_max_bars` | `20` | 플래그 최대 통합 바 수; 초과 시 직사각형으로 전환(StockCharts 규칙). |
| `flag_retracement_max` | `0.50` | 플래그 바디의 최대 되돌림(폴 범위 대비 50%). 초과 시 플래그가 아닌 잠재적 반전으로 간주. |
| `breakout_vol_ratio` | `1.5` | 돌파 바 볼륨이 20바 평균 볼륨의 이 배수 이상이어야 돌파 확인(컵 앤 핸들은 1.4). |
| `channel_lookback_pivots` | `10` | 직사각형/채널 경계 피팅에 사용할 교번 피벗 수. |
| `price_buffer` | `0.002` | 웨지/삼각형 형성 중 경계 위반 판정 여부 기준 분율(0.2%). |
| `cup_lookback_bars` | `120` | 컵 형성 탐색 최대 바 윈도우(기본 120바 ≈ 일봉 4~6개월). |
| `cup_rim_tolerance` | `0.03` | 오른쪽 림 가격이 왼쪽 림 가격 대비 허용 편차(3% 아래 또는 3% 위까지). |
| `cup_depth_min` | `0.12` | 컵 깊이 최솟값(왼쪽 림 가격 대비 12%, O'Neil 원본 IBD 기준). |
| `cup_depth_max` | `0.33` | 컵 깊이 최댓값(왼쪽 림 가격 대비 33%, O'Neil 원본 IBD 기준). 크립토는 0.50 고려. |
| `neckline_slope_limit` | `-0.005` | H&S 넥라인 바당 기울기; 이 임계값보다 음수(급격한 하향)이면 목표가 산출 신뢰성 저하로 거부. 상향 경사 넥라인(slope > 0)은 항상 허용. |

**출력 필드** — `pattern_type`, `direction`, `ts_start`, `ts_end`, `ts_breakout`, `zone_low`, `zone_high`, `neckline_price`, `target_price`, `pivot_sequence`, `strength`, `mitigated`, `ts_mitigated`, `trend_context`, `trendline_r_squared_top`, `trendline_r_squared_bot`, `pole_range`, `volume_ratio_at_breakout`, `apex_bar_index`

**진입 관련성** — 차트패턴은 세 가지 타이밍 시그널을 제공한다: (1) **돌파 전 대기(PRE-BREAKOUT WAIT)** — 패턴이 탐지되었으나 `ts_breakout`이 None인 경우, 진입 추천기는 대기해야 하며 통합 바디 내에서 진입하지 않는다. (2) **돌파 진입(BREAKOUT ENTRY)** — `ts_breakout`이 설정되고 `volume_ratio_at_breakout >= breakout_vol_ratio`이면 돌파 바 종가에서 진입 트리거; 반전 패턴은 넥라인 돌파 바, 지속 패턴은 경계 돌파 바. (3) **회피(AVOID)** — `strength < 0.4`(낮은 r_squared, 빈약한 대칭성, 낮은 볼륨 비율) 또는 `mitigated==True` 또는 apex까지 거리의 75% 초과 지점에서 돌파(소멸 돌파) 시 신호를 AVOID로 표시. **풀백 재진입**: 돌파 후 가격이 넥라인/경계까지 풀백하고 유지되면(다음 바가 패턴 방향으로 복귀하여 종가) 이는 보조 진입으로, `risk=neckline ± ATR(1)`의 타이트한 리스크와 함께 `'pullback_retest'`로 태깅한다. 컵 앤 핸들의 경우 진입은 볼륨 확인이 동반된 돌파 바에서 오른쪽 림(핸들 돌파점) 위이며, 단순히 핸들 고점 위가 아니다.

**컨플루언스** —
- 강세 패턴(Inverse H&S, Double Bottom, Ascending Triangle, Falling Wedge, Bull Flag, Cup-and-Handle, 강세 직사각형 돌파) → bullish 바이어스, **가중치 0.65**
- 약세 패턴(H&S Top, Double Top, Descending Triangle, Rising Wedge, Bear Flag, 약세 직사각형 하향 돌파) → bearish 바이어스, **가중치 0.65**
- 대칭 삼각형 → 돌파 방향 확정 전 neutral, **가중치 0.40**; 돌파 방향 확정 후 **0.65**
- `strength < 0.5` 또는 패턴이 지배적인 `trend_context`에 역행(예: 확인된 하락추세 중 더블 탑 — 실제로는 베어 플래그일 수 있음)이면 컨플루언스 가중치를 **0.35**로 하향. 볼륨 스파이크 확인과 멀티 타임프레임 정렬을 적용해 최종 0~1 합산 점수를 산출한다.

**거짓신호 가드** —
- **미래 편향 가드(핵심)**: 인덱스 i의 피벗은 바 `i+swing_lookback`이 완전히 닫힌 후에만 확정된다. 라이브 피드에서는 필요한 모든 우측 확인 바가 존재할 때만 피벗 의존 패턴 시그널을 발행한다. 현재 닫힌 바 인덱스 너머를 절대 참조하지 않는다.
- 피크/골 간 최소 `min_peak_separation`바 보장; 더 적은 바의 단일 스파이크 재테스트는 노이즈지 유효 패턴이 아니다.
- 더블 탑/바텀에서의 볼륨: 두 번째 피크 볼륨이 첫 피크보다 감소해야 함(더블 탑) — 두 번째 피크 볼륨이 같거나 높으면 패턴 품질이 낮아져 `strength < 0.5` 처리. 더블 바텀의 두 번째 골 볼륨 확인은 선호되나 덜 엄격함.
- H&S 탑의 경우: RS_low(피벗 i4, 헤드와 오른쪽 어깨 사이 골)가 LS_low(피벗 i2, 왼쪽 어깨와 헤드 사이 골)보다 낮으면 거부 — V자형 형성을 나타냄. 또한 오른쪽 어깨 고점이 헤드 고점보다 높으면 거부.
- 삼각형: `apex_bar_index <= current bar`이면 패턴 만료; 거부 또는 mitigated 처리. apex까지 거리의 75% 초과 시 소멸 패턴 — AVOID 표시.
- 웨지: 형성 중 `price_buffer` 초과 바디 이탈이 있으면 즉시 무효화.
- 플래그: 플래그 바디 되돌림이 폴의 50% 초과 시 잠재적 반전이지 지속 패턴이 아님; 플래그 거부.
- 컵 앤 핸들: V자형 컵 거부; 컵 바의 최소 30%가 컵 깊이의 10% 이내 바닥 영역 내 종가를 가져야 함.
- 모든 지속 패턴(플래그, 페넌트, 삼각형, 직사각형)에서 통합 중 볼륨 수축 필수; 형성 중 볼륨이 상승 추세이면 `strength`를 0.2 감소.
- 일봉 한정 직사각형-플래그 재분류: 패턴 스팬 < 15바 AND 선행 강한 방향성 폴 → 플래그로 재분류. 인트라데이 바에는 적용하지 않음.
- 패턴의 `zone_low`~`zone_high` 스팬이 ATR(14)의 1배 미만이면 패턴 시그널 발행하지 않음(너무 얕아 의미 없음).
- 추세선 품질 게이트: `trendline_fit_tolerance`(r_squared 게이팅)는 라인당 피벗 터치 3개 이상일 때만 적용. 2점 추세선은 항상 `r_squared=1.0`이며 고품질 피팅으로 취급해선 안 됨.
- H&S 및 역 H&S: 상향/하향 경사 넥라인 모두 정식 유효. `neckline_slope < neckline_slope_limit`(급격한 하향)인 경우만 거부. 상향 경사 넥라인(slope > 0)은 항상 허용.

**함정** —
- **미래 편향(핵심)**: `swing_lookback=2`는 피벗이 발생 2바 후에 확정됨을 의미한다. 이후 모든 패턴 탐지는 미확정 N개 우측 바를 절대 참조해선 안 된다. 라이브/백테스트 시스템에서 패턴 시그널은 바 i가 아닌 `i+swing_lookback`으로 날짜를 부여해야 한다.
- `swing_lookback=2`는 노이즈 많은 15분/1시간 데이터에서 공격적; 인트라데이에는 `swing_lookback=3` 또는 4를 고려해 허위 피벗을 줄여야 한다.
- 3% `peak_tolerance`는 흔히 인용되는 임계값이지만 가격 레벨에 무관하다. 고변동성 환경(크립토)에서는 고정 비율 대신 ATR(14)/price 스케일링을 고려한다.
- H&S 시간 대칭: `time_sym_ratio=2.5`는 `max/min` 스팬 비율을 사용한다. 이전 스펙의 공식 `abs((i3-i1)-(i5-i3))/(i3-i1) <= ratio`는 동일하지 않다 — 다른 정규화를 사용하므로 오른쪽 스팬이 왼쪽의 2.5배이면 `abs(L-2.5L)/L = 1.5`로 2.5가 아닌 값이 나온다. 직접 해석이 가능한 `max(left_span,right_span)/min(left_span,right_span) <= time_sym_ratio`를 사용하라.
- 컵 깊이: O'Neil의 정식 IBD 범위는 일부 이차 출처에서 언급하는 15~35%가 아닌 12~33%다. 주식에는 `cup_depth_min=0.12, cup_depth_max=0.33` 사용. 크립토는 더 깊은 조정이 일반적이므로 `cup_depth_max=0.50`도 합리적이다.
- 직사각형 대 실제 경사 채널: 현재 알고리즘은 `flat_slope_threshold`로 둘 다 직사각형으로 분류한다. 진정한 경사 채널(비제로 기울기, 평행 추세선)은 별도 분기가 필요하다: 두 기울기가 비제로이고 같은 부호이며 평행(`|m_top - m_bot| / max(|m_top|, |m_bot|) < 0.3`). 상승 평행 채널은 강세 지속; 하락 평행 채널은 약세 지속.
- 볼륨 규칙은 거래소 볼륨 데이터에 워시 트레이딩이 포함될 수 있는 크립토에서 신뢰도가 낮다; 크립토 시장에서는 볼륨 시그널 가중치를 0.5로 적용하는 것을 고려한다.
- 페넌트 대 대칭 삼각형: 알고리즘상 유일한 차이는 확인된 선행 폴의 존재 여부다; 둘 다 동일한 추세선 기하학을 공유한다. 명시적 폴 탐지 없이는 혼동된다.
- 최소제곱 추세선 피팅을 정확히 2개 피벗 점에 적용하면 실제 선형성과 무관하게 `r_squared=1.0`이 나온다; `trendline_fit_tolerance` 품질 게이트는 3개 이상 피벗 터치 시에만 적용하라.
- 돌파 확인을 단일 종가 초과/미만으로 사용하면 휩소가 발생할 수 있다; 설정 가능한 `breakout_confirm_bars` 파라미터(기본 1, 2로 설정 가능)로 고신뢰도 진입을 위한 연속 2회 종가 방식을 활성화한다.
- 패턴 레이블 충돌: 하락추세 컨텍스트에서 형성되는 상승 웨지는 반전이 아닌 약세 지속이다 — `trend_context`를 패턴 방향과 항상 교차 참조해 잘못된 레이블을 방지하라.
- STEP 11의 직사각형 돌파 버퍼는 0.003(0.3%)이지 3%가 아니다. StockCharts의 3% 수치는 확인 필터 옵션(가격이 적어도 3% 이상 돌파해야 함)으로, 자유 통과 노이즈 버퍼가 아니다. 0.3%를 최소 요구 돌파로 사용하는 것은 StockCharts 의도와 구분되는 보수적이지만 방어 가능한 구현 선택이다.
- H&S 거짓 양성 가드 표현: '오른쪽 어깨 저점이 헤드 저점 아래'는 구체적으로 RS_low(피벗 i4, 헤드와 오른쪽 어깨 사이 골) 대 LS_low(피벗 i2, 왼쪽 어깨와 헤드 사이 골)를 지칭해야 한다 — 오른쪽 어깨 피크 자체의 절대 저점이 아니다. 코드에서 피벗 레이블을 명확히 하라.

**참고** —
- https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-patterns/head-and-shoulders-bottom
- https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-patterns/flag-pennant
- https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-patterns/rectangle
- https://liquidity-provider.com/articles/triangle-patterns-in-trading-ascending-descending-symmetrical-guide/
- https://hub.algotrade.vn/knowledge-hub/head-and-shoulders-pattern/
- https://forextester.com/blog/cup-and-handle-pattern/
- https://trendspider.com/learning-center/chart-patterns-double-bottoms-and-tops/
- https://www.mql5.com/en/articles/21518
- https://www.luxalgo.com/blog/classic-chart-patterns-a-trading-essentials-guide/
- https://patents.google.com/patent/CA2403699A1/en
- https://www.luxalgo.com/blog/how-volume-confirms-breakouts-in-trading/
- https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-patterns/symmetrical-triangle
- https://school.stockcharts.com/doku.php?id=chart_analysis%3Achart_patterns%3Arectangle_continuation
- https://traderlion.com/technical-analysis/cup-and-handle-pattern/
- https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-patterns/cup-with-handle
- https://www.aaii.com/journal/article/predicting-short-term-trends-the-cup-with-handle-pattern
- https://en.wikipedia.org/wiki/Wedge_pattern
- https://en.wikipedia.org/wiki/Double_top_and_double_bottom
- https://en.wikipedia.org/wiki/Head_and_shoulders_(chart_pattern)
- https://trendspider.com/learning-center/what-is-a-wedge-and-what-are-the-rising-and-falling-wedge-patterns/
- https://www.thepatternsite.com/Apex.html
- https://www.researchgate.net/publication/282700493_Algorithm_for_detection_and_confirmation_of_head_and_shoulders_pattern_with_neckline

---

## 9. 캔들패턴 (candlestick_patterns)

**정의** — 캔들패턴(Candlestick Patterns)은 하나 이상의 OHLCV 바를 기반으로 가격 행동의 심리적 전환점을 식별하는 정형화된 기술적 분석 도구다. 18세기 일본 쌀 시장에서 유래하였고, 스티브 니슨(Steve Nison)이 서구에 현대화·체계화하여 소개한 분석법을 기준으로 한다. 바디(시가~종가 범위)와 위크(고가~저가에서 바디를 뺀 부분)의 비율, 방향, 연속성으로 패턴을 정의하며, 단봉(1개), 쌍봉(2개), 삼봉(3개) 패턴으로 분류된다. 각 패턴은 반드시 선행 추세 컨텍스트와 함께 해석해야 유효하며, 패턴만으로 진입 시그널이 되지 않는다. 반드시 지지/저항 레벨, 거래량, 멀티 타임프레임 컨텍스트와 함께 활용해야 하며, 신뢰도는 단봉 55~65%, 쌍봉 60~70%, 삼봉 65~75% 수준이고 S/R 레벨과 결합 시 상향된다.

**탐지 알고리즘** —

1. **STEP 0 — 공통 헬퍼 함수 정의**: 모든 패턴 탐지기가 사용하는 헬퍼 함수. `statistics` 모듈을 임포트한다.
   ```
   import statistics
   def body(b): return abs(b.close - b.open)
   def range_(b): return b.high - b.low if b.high != b.low else 1e-10
   def upper_wick(b): return b.high - max(b.open, b.close)
   def lower_wick(b): return min(b.open, b.close) - b.low
   def body_pct(b): return body(b) / range_(b)   # 0..1
   def is_bull(b): return b.close >= b.open
   def is_bear(b): return b.close < b.open
   def body_mid(b): return (b.open + b.close) / 2.0
   def candle_mid(b): return (b.high + b.low) / 2.0
   ```

2. **STEP 0b — 방향 분류기 정의 (STEP 5 스캔 루프 전 필수 정의)**: `classify_direction()` 함수는 STEP 5 스캔 루프에서 호출되므로 루프 전에 반드시 정의해야 한다.
   ```
   BULLISH_PATTERNS = {'hammer','inverted_hammer','bull_marubozu','dragonfly_doji',
                       'bullish_engulfing','bullish_harami','piercing_line',
                       'tweezer_bottom','morning_star','three_white_soldiers'}
   BEARISH_PATTERNS = {'hanging_man','shooting_star','bear_marubozu','gravestone_doji',
                       'bearish_engulfing','bearish_harami','dark_cloud_cover',
                       'tweezer_top','evening_star','three_black_crows'}
   def classify_direction(pattern_name: str) -> str:
       if pattern_name in BULLISH_PATTERNS: return 'bullish'
       if pattern_name in BEARISH_PATTERNS: return 'bearish'
       return 'neutral'   # doji, spinning_top
   ```

3. **STEP 1 — 선행 추세 탐지**: 모든 컨텍스트 의존 패턴에 사용. 바 목록(ts 오름차순)과 현재 인덱스 i가 주어지면, N=`trend_lookback`(기본 5)바를 뒤돌아보고 추세 분류. 고점 고-저, 저점 고-저 패턴을 카운팅하여 score >= 2이면 'up', <= -2이면 'down', 그 외 'neutral' 반환.
   ```
   def prior_trend(bars, i, N=5):
       if i < N: return 'neutral'
       window = bars[i-N:i]
       highs = [b.high for b in window]
       lows = [b.low for b in window]
       hh = sum(1 for j in range(1, len(highs)) if highs[j] > highs[j-1])
       lh = sum(1 for j in range(1, len(highs)) if highs[j] < highs[j-1])
       hl = sum(1 for j in range(1, len(lows)) if lows[j] > lows[j-1])
       ll = sum(1 for j in range(1, len(lows)) if lows[j] < lows[j-1])
       score = (hh + hl) - (lh + ll)
       if score >= 2: return 'up'
       if score <= -2: return 'down'
       return 'neutral'
   ```

4. **STEP 2 — 단봉(Single-Candle) 패턴 탐지** (인덱스 i의 바 평가):

   **마루보즈(Marubozu)**: `body_pct(b) >= 0.95`이고 양쪽 위크 < `shadow_pct(0.05) * body(b)`. 강세봉이면 `'bull_marubozu'`, 약세봉이면 `'bear_marubozu'`. 위크 없는 강한 모멘텀 봉.

   **도지(Doji)**: `body_pct(b) <= doji_body_threshold(0.05)`. 시가≈종가인 방향성 없는 봉. `'doji'` 반환.

   **잠자리 도지(Dragonfly Doji)**: `body_pct(b) <= 0.05`이고 상단 위크 `<= upper_max(0.05) * range_(b)`, 하단 위크 `>= 0.60 * range_(b)`. 시가≈종가≈고가, 긴 하단 위크. `'dragonfly_doji'` 반환.

   **묘비 도지(Gravestone Doji)**: `body_pct(b) <= 0.05`이고 하단 위크 `<= lower_max(0.05) * range_(b)`, 상단 위크 `>= 0.60 * range_(b)`. 시가≈종가≈저가, 긴 상단 위크. `'gravestone_doji'` 반환.

   **팽이(Spinning Top)**: `0.05 <= body_pct(b) <= body_max(0.30)`이고 양쪽 위크 각각 `>= wick_min_ratio(2.0) * body(b)`. 중간 크기 바디, 양방향 긴 위크, 대칭적 불확실성. `'spinning_top'` 반환.

   **해머(Hammer)**: `prior_trend(bars, i) == 'down'` 필수; `body_pct(b) <= body_max(0.35)`; 하단 위크 `>= wick_ratio(2.0) * body(b)`(긴 하단 위크); 상단 위크 `<= upper_max(0.10) * range_(b)`(미미한 상단 위크); 바디 하단 `= min(open,close)`이 `b.low + 60% of range` 이상(바디가 범위 상단 부분에 위치). `'hammer'` 반환.

   **행잉 맨(Hanging Man)**: 해머와 구조 동일하나 `prior_trend(bars, i) == 'up'` 필수. 동일한 바디 위치, 위크 비율 조건 적용. `'hanging_man'` 반환.

   **슈팅 스타(Shooting Star)**: `prior_trend(bars, i) == 'up'` 필수; `body_pct(b) <= body_max(0.35)`; 상단 위크 `>= wick_ratio(2.0) * body(b)`(긴 상단 위크); 하단 위크 `<= lower_max(0.10) * range_(b)`; `body_top = max(open,close)`이고 `b.high - body_top >= 0.60 * range_(b)`(상단 위크가 범위의 60% 이상 지배). `'shooting_star'` 반환.

   **역 해머(Inverted Hammer)**: `prior_trend(bars, i) == 'down'` 필수; `body_pct(b) <= body_max(0.35)`; 상단 위크 `>= wick_ratio(2.0) * body(b)`; 하단 위크 `<= lower_max(0.10) * range_(b)`; **바디 위치 확인(필수)**: `body_top = max(open,close)`이고 `b.high - body_top >= 0.60 * range_(b)` (슈팅 스타의 바디 위치 확인과 동일; 이 조건 없으면 잘못된 검출). `'inverted_hammer'` 반환. **약한 시그널이므로 다음 바 `close > b.high` 확인 필수.**

5. **STEP 3 — 쌍봉(Dual-Candle) 패턴 탐지** (`bars[i-1]`과 `bars[i]` 평가):

   **강세 엔걸핑(Bullish Engulfing)**: `prior_trend(bars, i) == 'down'`; `is_bear(prev) AND is_bull(curr)`; `curr.open <= prev.close`(curr가 prev 종가 이하에서 시작); `curr.close >= prev.open`(curr가 prev 시가 이상에서 마감); `body(curr) > body(prev)`. 두 번째 봉의 바디가 첫 번째 봉의 바디를 완전히 감싼다.

   **약세 엔걸핑(Bearish Engulfing)**: `prior_trend(bars, i) == 'up'`; `is_bull(prev) AND is_bear(curr)`; `curr.open >= prev.close`; `curr.close <= prev.open`; `body(curr) > body(prev)`.

   **강세 하라미(Bullish Harami)**: `prior_trend(bars, i) == 'down'`; `is_bear(prev) AND is_bull(curr)`; curr의 바디가 prev 바디 내에 완전히 포함됨(`curr_low_body >= prev_low_body AND curr_high_body <= prev_high_body`); `body_pct(prev) >= 0.60`(첫 봉은 큰 약세봉). **위크는 넘쳐도 되지만 바디 경계는 준수해야 함.**

   **약세 하라미(Bearish Harami)**: `prior_trend(bars, i) == 'up'`; `is_bull(prev) AND is_bear(curr)`; 동일한 바디 포함 조건; `body_pct(prev) >= 0.60`.

   **피어싱 라인(Piercing Line)**: `prior_trend(bars, i) == 'down'`; `is_bear(prev) AND is_bull(curr)`; `curr.open < prev.close`(curr가 prev 종가 아래에서 시작 — 갭 다운 또는 prev 종가와 같은 위치 허용 안 됨); `curr.close > prev_mid`(curr가 prev 바디 중간점보다 **엄격히** 위에서 마감, 등호 불가); `curr.close < prev.open`(완전한 엔걸핑이 아님).

   **다크 클라우드 커버(Dark Cloud Cover)**: `prior_trend(bars, i) == 'up'`; `is_bull(prev) AND is_bear(curr)`; `curr.open > prev.close`(갭 업 필요); `curr.close < prev_mid`(prev 바디 중간점보다 **엄격히** 아래에서 마감, 등호 불가); `curr.close > prev.open`(완전한 엔걸핑이 아님).

   **트위저 바텀(Tweezer Bottom)**: `prior_trend(bars, i) == 'down'`; `is_bear(prev) AND is_bull(curr)`; `abs(prev.low - curr.low) <= tol_pct(0.003) * prev.low`. 두 봉의 저가가 거의 일치.

   **트위저 탑(Tweezer Top)**: `prior_trend(bars, i) == 'up'`; `is_bull(prev) AND is_bear(curr)`; `abs(prev.high - curr.high) <= tol_pct(0.003) * prev.high`.

6. **STEP 4 — 삼봉(Triple-Candle) 패턴 탐지** (`bars[i-2]`, `bars[i-1]`, `bars[i]` 평가):

   **모닝 스타(Morning Star)**: `prior_trend(bars, i-2) == 'down'`(**필수**: i-2에서 호출하여 c1 이전의 추세를 확인); `is_bear(c1) AND body_pct(c1) >= 0.50`(큰 약세봉); `body_pct(c2) <= star_body_max(0.30)`(작은 별 봉); `is_bull(c3)`; `c3.close >= (c1.open + c1.close) / 2.0`(c3가 c1 바디의 50% 이상 회복); 별 봉 바디가 c1 종가 아래에 완전히 위치: `max(c2.open, c2.close) < c1.close`. 24시간 크립토/외환에서 갭이 드물므로 갭 조건 대신 바디 분리 규칙을 사용함.

   **이브닝 스타(Evening Star)**: `prior_trend(bars, i-2) == 'up'`; `is_bull(c1) AND body_pct(c1) >= 0.50`; `body_pct(c2) <= star_body_max(0.30)`; `is_bear(c3)`; `c3.close <= (c1.open + c1.close) / 2.0`(c3가 c1 바디의 50% 이상 하락); 별 봉 바디가 c1 종가 위에 완전히 위치: `min(c2.open, c2.close) > c1.close`.

   **세 백병사(Three White Soldiers)**: c1, c2, c3 모두: `is_bull(c)`, `body_pct(c) >= body_min(0.50)`, `upper_wick(c) <= upper_wick_max(0.15) * range_(c)`. c2 시가가 c1 바디 내: `c1.open <= c2.open <= c1.close`; c3 시가가 c2 바디 내: `c2.open <= c3.open <= c2.close`; `c2.close > c1.close AND c3.close > c2.close`.

   **세 흑까마귀(Three Black Crows)**: c1, c2, c3 모두: `is_bear(c)`, `body_pct(c) >= body_min(0.50)`, `lower_wick(c) <= lower_wick_max(0.15) * range_(c)`. c2 시가가 c1 바디 내: `c1.close <= c2.open <= c1.open`; c3 시가가 c2 바디 내: `c2.close <= c3.open <= c2.open`; `c2.close < c1.close AND c3.close < c2.close`.

7. **STEP 5 — 메인 스캔 루프**: `trend_lookback + 2`부터 시작(단순히 `trend_lookback`이 아님). 이렇게 해야 삼봉 패턴이 `prior_trend(bars, i-2)`를 호출할 때 항상 `i-2 >= trend_lookback` 조건을 만족하여 이른 바에서 잘못된 'neutral' 반환을 방지한다.
   ```
   results = []
   for i in range(trend_lookback + 2, len(bars)):
       for detector in [is_marubozu, is_doji, is_dragonfly_doji, is_gravestone_doji, is_spinning_top]:
           r = detector(bars[i])
           if r: results.append({'ts': bars[i].ts, 'pattern': r,
                                  'direction': classify_direction(r), 'bar_i': i})
       for detector in [is_hammer, is_hanging_man, is_shooting_star, is_inverted_hammer]:
           r = detector(bars, i)
           if r: results.append({'ts': bars[i].ts, 'pattern': r,
                                  'direction': classify_direction(r), 'bar_i': i})
       for detector in [is_bullish_engulfing, is_bearish_engulfing, is_bullish_harami,
                        is_bearish_harami, is_piercing_line, is_dark_cloud_cover,
                        is_tweezer_bottom, is_tweezer_top]:
           r = detector(bars, i)
           if r: results.append({'ts': bars[i].ts, 'pattern': r,
                                  'direction': classify_direction(r), 'bar_i': i})
       for detector in [is_morning_star, is_evening_star,
                        is_three_white_soldiers, is_three_black_crows]:
           r = detector(bars, i)
           if r: results.append({'ts': bars[i].ts, 'pattern': r,
                                  'direction': classify_direction(r), 'bar_i': i})
   ```

8. **STEP 6 — 강도 점수화**: `strength 1~3`을 세 요소로 결정. (1) 시그널 봉의 `body_pct`가 직전 10바 평균보다 1.2배 초과이면 +1. (2) 볼륨이 직전 10바 평균의 `strength_vol_ratio(1.5)`배 초과이면 +1. (3) 패턴 클래스: 삼봉 패턴이면 +1, 쌍봉 패턴이면 0, 단봉 패턴이면 **-1(수정됨 — 원본 스펙의 `score -= 0`은 no-op 버그였음)**. 최종: `max(1, min(3, score + 1))`.

**파라미터** —

| name | default | 의미 |
|------|---------|------|
| `doji_body_threshold` | `0.05` | 도지 분류를 위한 최대 바디/범위 비율(5%). |
| `marubozu_body_min` | `0.95` | 마루보즈의 최소 바디/범위 비율(95%). |
| `marubozu_shadow_pct` | `0.05` | 마루보즈에서 허용되는 최대 위크 비율(바디 대비 5%). |
| `spinning_top_body_max` | `0.30` | 팽이의 최대 바디/범위 비율(30%); 도지와 구분을 위해 최소 5% 필요. |
| `spinning_top_wick_min_ratio` | `2.0` | 팽이에서 각 위크가 바디 길이의 최소 배수. |
| `hammer_wick_ratio` | `2.0` | 해머/행잉 맨의 최소 하단 위크 대 바디 비율. |
| `hammer_body_max` | `0.35` | 해머/행잉 맨의 최대 바디/범위 비율(35%). |
| `hammer_upper_max` | `0.10` | 해머/행잉 맨의 최대 상단 위크(전체 범위의 10%). |
| `shooting_star_wick_ratio` | `2.0` | 슈팅 스타/역 해머의 최소 상단 위크 대 바디 비율. |
| `shooting_star_body_max` | `0.35` | 슈팅 스타/역 해머의 최대 바디/범위 비율(35%). |
| `shooting_star_lower_max` | `0.10` | 슈팅 스타/역 해머의 최대 하단 위크(전체 범위의 10%). |
| `trend_lookback` | `5` | 선행 추세 판단에 사용되는 룩백 바 수. 스캔 루프는 `trend_lookback+2`에서 시작. |
| `harami_first_body_min` | `0.60` | 하라미 첫 번째(모체) 봉의 최소 바디/범위 비율. |
| `engulfing_body_strict` | `True` | True이면 두 번째 봉의 바디가 첫 번째 봉보다 엄격히 커야 함. |
| `piercing_penetration` | `0.50` | 피어싱 라인/다크 클라우드 커버에서 두 번째 봉이 첫 번째 봉 바디에 침투해야 하는 최소 비율(니슨 정식 정의: 중간점 엄격 초과). |
| `tweezer_tol_pct` | `0.003` | 트위저 패턴 고/저가 매칭 허용 가격 오차(가격 레벨의 0.3%). |
| `morning_star_body_max` | `0.30` | 모닝/이브닝 스타 중간(별) 봉의 최대 바디/범위 비율. |
| `morning_star_penetration` | `0.50` | 세 번째 봉이 첫 번째 봉 바디에 침투해야 하는 최소 비율. |
| `three_soldiers_body_min` | `0.50` | 세 백병사/세 흑까마귀의 각 봉 최소 바디/범위 비율. |
| `three_soldiers_wick_max` | `0.15` | 세 백병사(상단 위크)/세 흑까마귀(하단 위크)의 최대 반대 방향 위크(범위 대비 15%). |
| `strength_vol_ratio` | `1.5` | 볼륨 보너스 점수 부여를 위한 10바 평균 대비 배수. |

**출력 필드** — `ts`, `symbol`, `market`, `freq`, `pattern_name`, `direction`, `bar_i`, `prior_trend`, `strength`, `body_pct_signal`, `vol_ratio`, `mitigated`

**진입 관련성** — 캔들패턴은 다음 규칙에 따라 진입 타이밍 결정에 사용한다. (1) 반전 시그널 패턴(hammer, engulfing, morning star 등) 감지 시 해당 바 종가 또는 다음 바 시가에서 진입을 준비하되, 반드시 **확인(confirmation)** 조건이 충족되어야 실제 진입한다. 확인 조건: 강세 패턴은 다음 바 `close > 패턴 바 high`, 약세 패턴은 다음 바 `close < 패턴 바 low`. (2) Doji/spinning top은 방향성 없음 → 진입 대기 신호: 다음 바 방향 확인 후 진입. (3) Marubozu는 강한 모멘텀 신호; 추세 방향과 일치 시 돌파 후 즉시 진입 가능. (4) Three white soldiers / three black crows는 확인 없이 패턴 완성 바 다음 시가 진입 가능(`strength >= 2` 필수). (5) Inverted hammer는 약한 신호로 단독 진입 금지; 다음 바 `close > 패턴 바 high` 확인 필수. (6) `strength=1` → WAIT, `strength=2` → ENTER_ON_CONFIRM, `strength=3` → ENTER_NOW. (7) 패턴이 주요 지지/저항 레벨, 피보나치 레벨(0.382/0.5/0.618), 또는 Volume Profile POC 근처(0.5% 이내)에서 발생하면 신뢰도를 한 등급 상향. (8) `mitigated=True`(이미 반응한 레벨)인 경우 AVOID.

**컨플루언스** — 방향성 바이어스: 강세 패턴(hammer, bullish engulfing, morning star, three white soldiers, piercing line, tweezer bottom, dragonfly doji, inverted hammer) = bullish. 약세 패턴(hanging man, bearish engulfing, evening star, three black crows, dark cloud cover, tweezer top, gravestone doji, shooting star) = bearish. 중립(doji, spinning top) = neutral. 추천 컨플루언스 가중치: 단봉 반전(doji/spinning top) = **0.15**, 단봉 방향성(hammer/shooting star 등) = **0.25**, 쌍봉(engulfing/harami/piercing/dark cloud/tweezer) = **0.35**, 삼봉(morning star/evening star/three soldiers/crows) = **0.45**. 이 가중치는 다른 시그널(ICT OB=0.35, FVG=0.25, MACD divergence=0.20, RSI oversold=0.15)과 합산 시 총합을 1.0으로 정규화하여 사용. 볼륨 확인이 동반되면 가중치 +0.05 추가.

**거짓신호 가드** —
- 선행 추세 확인 필수: 컨텍스트 의존 패턴(hammer, hanging man, shooting star, inverted hammer, engulfing, harami, morning/evening star)은 반드시 `prior_trend != 'neutral'`이어야 함. 스캔 루프는 `trend_lookback+2`에서 시작하여 삼봉 패턴의 `prior_trend(bars, i-2)` 호출이 항상 충분한 히스토리를 갖도록 보장; 반환된 추세가 'neutral'이면 `strength`를 1 감소하거나 스킵한다.
- 볼륨 확인: 패턴 바 볼륨이 직전 10바 평균 이상이어야 함; 저볼륨 패턴(`vol_ratio < 0.8`)은 구조와 무관하게 `strength=1` 처리.
- Doji와 spinning top은 추세 중에 지속 노이즈로 자주 나타남; 다음 바 방향성 종가 확인 전에는 행동하지 않는다.
- Inverted hammer는 약한 시그널이므로 절대 단독 진입 금지; 다음 바 `close > 패턴 바 high` 확인이 반드시 필요하다. 또한 바디가 범위 하단 부분에 위치해야 함(`b.high - body_top >= 60% of range`이 슈팅 스타의 바디 위치 확인과 대칭됨).
- 하라미 두 번째 봉 바디 포함 조건은 첫 번째 봉의 바디(open/close)에 대해 검증해야 하며, 위크는 벗어나도 허용되나 바디 범위를 벗어나면 패턴 무효.
- 모닝/이브닝 스타의 중간(별) 봉 바디는 첫 번째 봉 종가 아래/위에 **완전히** 위치해야 함(부분이 아닌 전체): 모닝 스타에서는 `max(c2.open,c2.close) < c1.close`; 이브닝 스타에서는 `min(c2.open,c2.close) > c1.close`. 갭이 드문 24시간 크립토 시장에서도 이 완화된 갭 규칙(갭 대신 바디 분리)은 여전히 필요하다.
- 세 백병사/세 흑까마귀가 연장된 추세(선행 추세 이미 10바+ 지속) 중에 나타나면 지속이 아닌 소진일 수 있음; `strength >= 2` 필수이며, 시그널 바에서 RSI > 75(과매수) 또는 < 25(과매도)이면 진입하지 않는다.
- 트위저 매칭 허용 오차는 적응형이어야 함: 고정 pip 값이 아닌 `tol_pct * bar.high` 또는 `bar.low`를 사용하여 고가 자산에서 과도한 트리거 방지.
- 마루보즈 거짓 시그널(갭 오픈 세션): `body(b) > ATR14 * 0.5` 조건으로 갭 아티팩트를 제외하여 검증한다.
- 엔걸핑과 하라미는 반대 봉 방향이 필요함; 같은 방향 엔걸핑(예: 강세 봉이 강세 봉을 엔걸핑)은 무효이며 원시 데이터에서 흔히 발생함.
- 피어싱 라인과 다크 클라우드 커버의 중간점 확인은 엄격한 부등호를 사용(등호 허용 불가); 중간점 등호는 불충분한 침투로 처리함(니슨 정식 정의).

**함정** —
- **수정된 버그 — `classify_direction()` 미정의**: 원본 드래프트에서 STEP 5 스캔 루프에서 호출되었으나 정의되지 않았다. 수정된 스펙은 STEP 0b에서 전체 강세/약세/중립 세트 매핑과 함께 명시적으로 정의한다.
- **수정된 버그 — 단봉 강도 점수화**: 원본 코드의 `score -= 0`은 no-op이었다. 의도된 `삼봉 > 쌍봉 > 단봉 = +1/0/-1` 구조가 구현되지 않았다. 단봉은 `score -= 1`로 수정되었다.
- **수정된 버그 — `is_inverted_hammer` 바디 위치 확인 누락**: 원본 스펙은 위크 비율과 바디 크기를 확인했으나 바디가 범위 하단 부분에 있는지 확인하지 않았다(`is_shooting_star`의 `body_top` 확인을 미러링). 추가됨: `body_top = max(open,close)`; `(high - body_top) < 0.60 * range_`이면 None 반환.
- **수정된 버그 — 피어싱 라인/다크 클라우드 커버 경계 조건**: 원본에서 중간점에서 `>`와 `<`의 엄격한 부등호를 사용하여 등호 통과를 허용했다. 다크 클라우드 커버는 `>=`(종가가 중간점보다 엄격히 아래), 피어싱 라인은 `<=`(종가가 중간점보다 엄격히 위)로 수정됨.
- **수정된 버그 — 스캔 루프 시작 인덱스**: 원본 루프는 `trend_lookback`(예: 5)에서 시작했다. 삼봉 패턴은 `prior_trend(bars, i-2)`를 호출하는데, `i=trend_lookback=5`일 때 `i-2=3`이고 `prior_trend`는 `i>=N=5`를 요구하므로 잘못된 'neutral' 반환이 발생했다. `trend_lookback+2`에서 시작하도록 수정됨.
- **변형 — 모닝/이브닝 스타의 갭 조건**: 니슨의 원본 정의는 일봉 주식 차트에서 c1과 별 봉 사이, 별 봉과 c3 사이의 갭을 요구한다. 24시간 크립토/외환에서는 봉 경계에서 갭이 드물다. 이 스펙은 완화된 '바디 분리' 규칙(별 바디가 c1 종가 아래/위에 완전히 위치)을 대리로 사용한다. 탐지된 모닝/이브닝 스타에 `gap_present` 불리언을 태깅하라: 모닝 스타에서 `abs(c1.close - max(c2.open,c2.close)) > 0`이면 True.
- **변형 — 세 백병사 열기 범위**: 정식 출처는 c2가 c1의 '중간점과 종가 사이'(바디 상단 절반)에서 열기를 선호한다. 이 스펙은 전체 바디 범위를 허용 열기 윈도우로 사용하는데 더 허용적이다. 더 엄격한 시그널을 원하면 `c2.open < c1.open` 가드를 `c2.open < body_mid(c1)`으로 교체하라.
- **변형 — 하라미 크기 상한**: 일부 출처(니슨)는 두 번째 봉이 첫 번째 봉의 25~30% 크기여야 한다고 요구한다. 이 스펙은 크기 상한 없는 완전한 포함 조건을 사용한다. 원하면 `max_body_ratio` 파라미터로 타이트하게 조정하라.
- **잠자리/묘비 도지 대 해머/슈팅 스타**: 구조적 차이는 `body_pct <= 5%`(도지) 대 소형이지만 비제로 바디(해머/슈팅 스타, 최대 35%). 같은 바에서 둘 다 발화되면 더 구체적인 서브타입을 선호하라(상단 위크 < 범위의 5% AND 하단 위크 > 범위의 60%이면 잠자리 도지가 일반 도지보다 우선).
- **타임프레임 의존성**: 1시간봉의 동일한 패턴은 일봉보다 훨씬 의미가 낮다; 탐지된 패턴에 항상 `freq` 필드를 태깅하고 컨플루언스 계산에서 일봉 패턴을 더 높게 가중치 부여하라.
- **미래 편향**: 패턴은 `bars[i].ts <= current_time`인 바에 대해서만 평가되어야 한다. 스캔 루프는 바 i가 완전히 닫힌 후에만 처리한다. 현재 형성 중인 바에서 절대 탐지기를 호출하지 않는다.
- **같은 바에서의 복수 패턴 동시 발생**은 유효하다(예: 한 바가 doji이면서 dragonfly_doji일 수 있음). 가장 구체적인 서브타입을 선호하라. 쌍봉/삼봉 패턴은 단봉 패턴과 `bars[i]`를 공유하는데, 중복 시 컨플루언스 점수화에서 쌍봉/삼봉이 우선한다.

**참고** —
- https://ia801802.us.archive.org/8/items/JapaneseCandlestickChartingTechniques2ndEditionSteveNison/Japanese%20Candlestick%20Charting%20Techniques%2C%202nd%20Edition%2C%20Steve%20Nison_text.pdf
- https://medium.com/@katoyebir/candlestick-pattern-detection-technique-7d2cab87f9ea
- https://ayratmurtazin.beehiiv.com/p/a-guide-to-identifying-candlestick-pattern-in-python-using-ta-lib-and-custom-formulas
- https://journalplus.co/patterns/marubozu/
- https://zerodha.com/varsity/chapter/single-candlestick-patterns-part-2/
- https://github.com/cm45t3r/candlestick
- https://www.strike.money/technical-analysis/morning-star
- https://docs.tradingmetrics.com/en/technical-analysis/trading-patterns/candlestick-patterns/bullish-patterns/morning-star
- https://enrichmoney.in/knowledge-center-chapter/dark-cloud-cover-piercing-candlestick-pattern
- https://www.litefinance.org/blog/for-beginners/how-to-read-candlestick-chart/hammer-candlestick-pattern/
- https://trendspider.com/learning-center/tweezer-tops-and-bottoms-a-traders-guide/
- https://ta-lib.github.io/ta-lib-python/func_groups/pattern_recognition.html
- https://www.chartmill.com/documentation/technical-analysis/candlestick-patterns/443-dragonfly-doji
- https://fxopen.com/blog/en/a-dragonfly-doji-candlestick-pattern-definition-interpretation-and-trading-strategies/
- https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/supported-indicators/candlestick-patterns/three-white-soldiers
- https://www.axi.com/int/blog/education/engulfing-candlestick-patterns
- https://www.litefinance.org/blog/for-beginners/how-to-read-candlestick-chart/morning-star-pattern/
- https://trendspider.com/learning-center/dark-cloud-cover-a-traders-guide/
- https://alchemymarkets.com/education/candlesticks/dark-cloud-cover/
- https://www.strike.money/technical-analysis/inverted-hammer
- https://fxopen.com/blog/en/how-to-use-the-inverted-hammer-pattern/
- https://www.litefinance.org/blog/for-beginners/how-to-read-candlestick-chart/three-white-soldiers-pattern/
- https://babypips.com/forexpedia/bullish-engulfing
- https://babypips.com/forexpedia/bearish-engulfing-pattern

## 10. 호가창 (L2 호가 분석) (Order Book Depth)

**정의** — 호가창(order book, L2 시장 데이터)은 거래소에서 대기 중인 모든 지정가 주문을 실시간으로 양방향으로 기록한 구조다. 매수(bid) 쪽은 가격 내림차순, 매도(ask) 쪽은 가격 오름차순으로 정렬된다. 여기서 파생되는 핵심 지표는 네 가지다.

(1) **호가 불균형 (Order Book Imbalance, OBI)**: `OBI = (bid_vol_N - ask_vol_N) / (bid_vol_N + ask_vol_N)` — 상위 N 레벨을 대상으로 하는 정적 스냅샷 기반 정규화 지표(범위 −1 ~ +1). 매수 압력이 강할수록 +1에 가깝고, 매도 압력이 강할수록 −1에 가깝다. OBI의 예측 반감기는 초 단위에서 약 1분 이내로 매우 짧다.

(2) **거래량 가중 중간가 (Volume Adjusted Mid Price, VAMP)**: `vamp = (bid_vol × ask + ask_vol × bid) / (bid_vol + ask_vol)` — 상위 레벨 수량과 반대편 가격을 교차 곱(cross-multiply)하여 공정 가격 방향성을 수치화한다. 이는 Stoikov의 완전 미세 가격(micro-price)과 구별되는 1차 근사다. Stoikov의 완전 미세 가격은 역사적 (불균형, 스프레드) 상태 전이에 기반한 마르코프 체인이 필요하며 6회 재귀 반복 후 수렴하는 별도 알고리즘이다.

(3) **유동성 벽 (Liquidity Wall)**: 단일 지정가 주문이 주변 레벨 대비 이상 수준으로 크게 적재된 가격대. 강력한 지지/저항으로 작용하거나 스푸핑(spoofing) 주문일 수 있다.

(4) **누적 깊이 곡선 (Cumulative Depth Curve)**: 최우선 호가(best price)에서 바깥쪽으로 일정 밴드 내 모든 레벨의 수량을 누적 합산한 값. 매수/매도 양측 깊이 비율로 단기 시장 쏠림을 측정한다.

**중요 구분**: OBI(정적 스냅샷 비율)와 OFI(Order Flow Imbalance, Cont et al. 2014)는 다른 지표다. OFI는 연속 틱 간 최우선 매수/매도 호가 대기량의 누적 변화량이다. 이 스펙은 OBI를 구현한다.

---

**탐지 알고리즘** —

1. **스냅샷 수집 (Step 1)**
   `ob = exchange.fetch_order_book(symbol, limit=ob_depth_levels)`를 ccxt로 호출한다. 반환 딕셔너리 키: `bids`(가격 내림차순 `[price, size]` 리스트), `asks`(가격 오름차순 `[price, size]` 리스트), `timestamp`(int ms, 일부 거래소는 `None` 반환 가능), `datetime`(ISO 문자열), `symbol`, `nonce`. 진행 전 검증: `len(ob['bids']) >= 1 and len(ob['asks']) >= 1`.

2. **최우선 호가 추출 (Step 2)**
   `best_bid = ob['bids'][0][0]`, `best_bid_vol = ob['bids'][0][1]`, `best_ask = ob['asks'][0][0]`, `best_ask_vol = ob['asks'][0][1]`. 교차 호가창(crossed book) 가드: `assert best_bid < best_ask` (성립하지 않으면 호가창이 낡은 스냅샷 — 폐기). 레벨 정렬 검증: `len >= 2`일 때 `ob['bids'][0][0] > ob['bids'][1][0]`(bid 내림차순) AND `ob['asks'][0][0] < ob['asks'][1][0]`(ask 오름차순) 확인. 일부 거래소 ccxt 어댑터가 역순으로 반환하는 버그가 보고된 바 있다(ccxt issue #24859).

3. **스프레드 계산 (Step 3)**
   `spread_abs = best_ask - best_bid`, `mid = (best_bid + best_ask) / 2.0`, `spread_pct = (best_ask - best_bid) / mid * 100`. 학술 마켓 마이크로구조 관례는 스프레드를 중간 가격(midprice)으로 정규화한다(최우선 매도 호가가 아님). `spread_is_wide = True` 조건: `spread_pct > spread_pct_threshold`(기본 0.10%). 주요 페어(BTC/USDT Binance)의 스프레드는 ~0.01%이지만, 알트코인은 1%를 초과할 수 있다. 스프레드가 넓으면 OBI 신호만으로 거래하지 않는다.

4. **거래량 가중 중간가 계산 (Step 4)**
   `vamp = (best_bid_vol * best_ask + best_ask_vol * best_bid) / (best_bid_vol + best_ask_vol)`. 이 수식은 각 측의 수량을 반대편 가격에 교차 곱(VAMP / Volume Adjusted Mid Price 관례)한다. 해석: `best_bid_vol >> best_ask_vol`이면 분자가 `best_bid_vol × best_ask`에 지배되어 vamp가 best_ask 쪽(mid보다 위)으로 당겨짐 — 강세 신호. 반대면 약세. `delta_vamp = vamp - mid`. 주의: 이 수식은 Stoikov의 완전 미세 가격이 아니다. Stoikov 미세 가격은 역사적 (불균형, 스프레드) 상태 전이에 기반한 마르코프 체인을 필요로 하며 6회 재귀 후 수렴한다 — 단일 스냅샷 호출로 구현 불가. VAMP는 진입 타이밍 목적에서 충분한 1차 근사이며, 하위 시스템에서 'micro-price'로 표기하면 혼동을 초래한다.

5. **평균 OBI 계산 (Step 5 — 균등 가중)**
   `N = min(obi_top_n, len(ob['bids']), len(ob['asks']))`. `bid_vol_N = sum(level[1] for level in ob['bids'][:N])`, `ask_vol_N = sum(level[1] for level in ob['asks'][:N])`. `total = bid_vol_N + ask_vol_N`, `obi_flat = (bid_vol_N - ask_vol_N) / total if total > 0 else 0.0`. 범위 [−1, 1].

6. **가중 OBI 계산 (Step 6 — 지수 감쇠)**
   `weights = [1.0 / (2**i) for i in range(N)]` — 즉 [1.0, 0.5, 0.25, ...]. 이 방식은 깊은 레벨을 기하급수적으로 낮게 가중치 부여한다. hftbacktest 같은 구현에서 사용되는 실무 관례이며, 특정 학술 처방은 아니다(MLOFI 문헌은 OLS 추정 가중치 사용). `weighted_bid = sum(ob['bids'][i][1] * weights[i] for i in range(N))`, `weighted_ask = sum(ob['asks'][i][1] * weights[i] for i in range(N))`, `w_total = weighted_bid + weighted_ask`, `obi_weighted = (weighted_bid - weighted_ask) / w_total if w_total > 0 else 0.0`.

7. **OBI 국면 분류 (Step 7 — 5단계 구간)**
   Cartea et al. 2018 기반 5구간 분류(TDS 크립토 OBI 연구에서 적용). `obi_weighted >= 0.6` → `STRONG_BID`, `obi_weighted >= 0.2` → `MILD_BID`, `obi_weighted <= -0.6` → `STRONG_ASK`, `obi_weighted <= -0.2` → `MILD_ASK`, 그 외 → `NEUTRAL`. 고변동성 환경에서는 강세 임계값을 0.8로 상향한다. 주의: 임계값 출처는 Cartea et al. / TDS 크립토 연구의 5구간 분류이며, OFI를 정의한 Cont et al. 2014와 혼동하지 않는다.

8. **누적 깊이 곡선 계산 (Step 8)**
   `band = mid * depth_band_pct / 100.0`. `bid_levels_in_band = [lv for lv in ob['bids'] if lv[0] >= mid - band]`, `ask_levels_in_band = [lv for lv in ob['asks'] if lv[0] <= mid + band]`. `cum_bid_depth = sum(lv[1] for lv in bid_levels_in_band)`, `cum_ask_depth = sum(lv[1] for lv in ask_levels_in_band)`. `depth_ratio = cum_bid_depth / cum_ask_depth if cum_ask_depth > 0 else float('inf')`. `depth_ratio > 1.0`이면 밴드 내 매수 측이 더 두텁다.

9. **유동성 벽 탐지 (Step 9)**
   각 측의 N 레벨에서 수량 중앙값 계산: `import statistics; bid_vols = [lv[1] for lv in ob['bids'][:N]]; ask_vols = [lv[1] for lv in ob['asks'][:N]]; bid_median = statistics.median(bid_vols); ask_median = statistics.median(ask_vols)`. 레벨의 수량이 `wall_size_multiplier × 중앙값`을 초과하면 '벽(wall)'으로 분류(기본 5.0배). `bid_walls = [(ob['bids'][i][0], ob['bids'][i][1]) for i in range(N) if ob['bids'][i][1] > wall_size_multiplier * bid_median]`, `ask_walls = [(ob['asks'][i][0], ob['asks'][i][1]) for i in range(N) if ob['asks'][i][1] > wall_size_multiplier * ask_median]`. `nearest_bid_wall = max(bid_walls, key=lambda x: x[0]) if bid_walls else None`, `nearest_ask_wall = min(ask_walls, key=lambda x: x[0]) if ask_walls else None`.

10. **스푸핑/플리커 경고 처리 (Step 10)**
    단일 스냅샷 탐지기는 스푸핑 확정 불가. 다음 조건 중 하나라도 성립하면 해당 벽을 `SUSPECT`로 표시: (a) 현재 스냅샷에는 벽이 존재하나 직전 스냅샷에는 없었음(연속 두 번의 ob 수집 비교). (b) 벽이 중간 가격에서 `wall_distance_pct`% 이상 떨어진 위치에 있음(원거리 주문은 체결 확률이 낮아 스푸핑 가능성 높음). `wall_is_suspect = True`로 표시. `SUSPECT` 벽은 지지/저항으로 사용하지 않는다.

11. **OBI z-score 표준화 (Step 11)**
    `obi_zscore_window` 샘플 크기의 롤링 덱(deque)을 유지한다. 입력은 `obi_weighted`(Step 7과 동일한 신호, `obi_flat` 아님). `len(deque) >= 10`이면: `mean_obi = statistics.mean(deque)`, `stdev_obi = statistics.stdev(deque)` (표본 표준편차, Bessel 보정, n-1 분모), `obi_zscore = (obi_weighted - mean_obi) / stdev_obi if stdev_obi > 0 else 0.0`. `statistics.pstdev`(모집단 표준편차, n 분모)를 사용하면 소규모 창에서 z-score가 과대 추정됨 — 롤링 창은 진행 중인 스트림의 표본이므로 `stdev`(표본)가 올바르다. 강한 신호 = `abs(obi_zscore) > 1.5`.

12. **타임스탬프 처리 (Step 12)**
    `ts = ob['timestamp'] if ob.get('timestamp') is not None else int(time.time() * 1000)`. 일부 거래소(초기 Binance 구현 등)는 `timestamp`로 `None`을 반환한다 — 로컬 시스템 시간(밀리초)으로 폴백.

13. **출력 레코드 조립 (Step 13)**
    아래 출력 필드를 모두 포함한 딕셔너리를 반환한다. 이 스냅샷과 시간상 가장 가까운 `PriceBar.ts`에 연결한다. `PriceBar`의 OHLCV 필드는 맥락(현재 세션 식별, 스프레드를 ATR과 비교 등)으로만 사용하며, 1차 입력은 전적으로 실시간 호가창 스냅샷이다.

---

**파라미터** —

| name | default | 의미 |
|------|---------|------|
| `ob_depth_levels` | `20` | ccxt `fetch_order_book`의 `limit` 파라미터. 주요 페어에는 20; 얇은 시장에는 10. Binance는 최대 5000 지원; 실용 범위는 10~50. |
| `obi_top_n` | `10` | OBI 계산에 포함할 레벨 수. 표준 연구에서는 서브초(sub-second)에 5, 초~분 단위에 10. `ob_depth_levels` 이하여야 한다. |
| `spread_pct_threshold` | `0.10` | OBI 신호를 실행 가능한 것으로 판단하는 최대 스프레드(%, mid 기준). BTC/USDT Binance는 ~0.01%; 알트코인은 1% 초과 가능. 이 임계값 초과 시 비유동적 호가창으로 판단. |
| `depth_band_pct` | `1.0` | 누적 깊이 곡선 적분용 중간 가격 기준 밴드 크기(%). 예: 1.0 = ±1%. HFT 환경에는 0.5, 저빈도에는 2.0. |
| `wall_size_multiplier` | `5.0` | N 레벨 수량 중앙값의 이 배수를 초과하는 레벨을 유동성 벽으로 분류. 업계 관행: 탐지 5배; 얇은 호가창에는 3배. |
| `wall_distance_pct` | `2.0` | 중간 가격에서 이 % 이상 떨어진 벽은 `SUSPECT`로 표시(스푸핑 가능성 높음). Step 10에서 적용. |
| `obi_zscore_window` | `60` | OBI z-score 표준화용 롤링 샘플 수. 1초당 1스냅샷 기준 60 = 1분 정규화 창. hftbacktest에서 1Hz 기준 1시간 창(3600)과 동일한 비례. |
| `obi_strong_threshold` | `0.6` | `STRONG_BID` 또는 `STRONG_ASK`를 선언하는 `obi_weighted` 임계값. Cartea et al. / TDS 크립토 연구 5구간 분류 기반. 고변동성 시 0.8로 상향. |
| `obi_mild_threshold` | `0.2` | `MILD` 국면 임계값. ±0.2 이내는 `NEUTRAL`. 5구간 θ={−1,−0.6,−0.2,0.2,0.6,1} 기반. |

---

**출력 필드** —

| 필드 | 타입 | 설명 |
|------|------|------|
| `ts` | int ms | 스냅샷 타임스탬프 (거래소 또는 로컬 시스템) |
| `symbol` | str | 심볼 (예: `BTC/USDT`) |
| `best_bid` | float | 최우선 매수 호가 |
| `best_ask` | float | 최우선 매도 호가 |
| `spread_abs` | float | 절대 스프레드 = best_ask − best_bid |
| `spread_pct` | float | 중간 가격 기준 스프레드(%) |
| `spread_is_wide` | bool | `spread_pct > spread_pct_threshold`이면 True |
| `mid` | float | 산술 중간 가격 = (best_bid + best_ask) / 2 |
| `vamp` | float | 거래량 가중 중간 가격 (VAMP) |
| `delta_vamp` | float | `vamp − mid` (양수 = 강세 방향 편향) |
| `obi_flat` | float | 균등 가중 OBI (범위 [−1, 1]) |
| `obi_weighted` | float | 지수 감쇠 가중 OBI (범위 [−1, 1]) |
| `regime` | str | `STRONG_BID` / `MILD_BID` / `NEUTRAL` / `MILD_ASK` / `STRONG_ASK` |
| `obi_zscore` | float\|None | 롤링 창 기준 z-score (abs > 1.5 = 강한 신호) |
| `cum_bid_depth_in_band` | float | 밴드 내 매수 측 누적 수량 |
| `cum_ask_depth_in_band` | float | 밴드 내 매도 측 누적 수량 |
| `depth_ratio` | float | `cum_bid / cum_ask` (> 1.0이면 매수 측 우세) |
| `bid_walls` | list[tuple] | 감지된 매수 벽 목록 (price, size) |
| `ask_walls` | list[tuple] | 감지된 매도 벽 목록 (price, size) |
| `nearest_bid_wall_price` | float\|None | 가장 가까운 매수 벽 가격 |
| `nearest_bid_wall_vol` | float\|None | 가장 가까운 매수 벽 수량 |
| `nearest_ask_wall_price` | float\|None | 가장 가까운 매도 벽 가격 |
| `nearest_ask_wall_vol` | float\|None | 가장 가까운 매도 벽 수량 |
| `wall_is_suspect` | bool | 벽이 스푸핑 의심 조건 충족 시 True |
| `direction` | str | 진입 방향 신호: `LONG` / `SHORT` / `WAIT` / `NEUTRAL` |
| `strength` | float | 신호 강도 (0~1; 구성 로직은 진입 관련성 참고) |

---

**진입 관련성** —

**롱 진입** 조건: (1) `obi_weighted >= obi_strong_threshold` (`STRONG_BID` 국면), AND (2) `delta_vamp > 0` (가중 중간 가격이 산술 중간 가격보다 위 → 매수 측 지정가 압력 확인), AND (3) `spread_is_wide == False`, AND (4) 중간 가격에서 0.5% 이내에 `SUSPECT`가 아닌 매도 벽 없음. 이 조합은 눈에 보이는 매수 지정가 수요가 우세하고 공정 가격이 상향 편향되었으며 스푸핑 장벽이 진입 경로에 없음을 의미한다.

**대기 (WAIT)** 조건: 국면이 `NEUTRAL`이거나, `spread_is_wide == True`(수수료가 우위를 잠식)이거나, `obi_zscore` 절댓값이 1.0 이하(신호 강도 부족).

**롱 회피** 조건: 국면이 `STRONG_ASK`이거나 `depth_band_pct`% 이내에 확인된(non-suspect) 매도 벽이 존재 — 벽이 시장 매수 주문을 흡수해 저항으로 작용한다.

**숏 진입**: 위 모든 조건을 반전하여 적용(`obi_weighted <= -obi_strong_threshold`, `delta_vamp < 0`, 매수 벽 없음).

**핵심 주의사항**: OBI는 매우 단기적인 신호(반감기 초 ~ 약 1분)다. 반드시 상위 타임프레임 신호(추세, SMC, 거래량 프로파일 등)에 의해 방향이 이미 확인된 봉 안에서 타이밍 필터로만 사용한다. OBI 단독으로 진입 트리거로 쓰지 않는다.

---

**컨플루언스** —

| 방향 | 조건 | 가중치 |
|------|------|--------|
| **강세** | `regime == STRONG_BID` (`obi_weighted >= 0.6`) | 0.25 |
| **약세** | `regime == STRONG_ASK` (`obi_weighted <= -0.6`) | 0.25 |
| **중립** | 그 외 국면 | 0 |

OBI는 마이크로구조 필터이며 1차 셋업 생성기가 아니다. SMC 오더 블록이나 VWAP 지지와 OBI가 정렬되면 진입 신뢰도를 높이고, OBI가 반대 방향이면 진입을 거부하는 방식으로 사용한다. 멀티 신호 스코어러에서 OBI 가중치는 0.25를 상한으로 설정한다 — 신호가 초~분 단위로 소멸하여 중기/장기 방향성 우위를 제공하지 않기 때문이다. 고변동성 국면 또는 확인된 스푸핑 이벤트 발생 시 가중치를 0.10으로 축소한다.

---

**거짓신호 가드** —

1. **스푸핑/플리커 벽 가드**: 가격이 도달하기 전에 사라지는 대형 지정가 주문은 실제 유동성이 아니다. 연속 스냅샷 비교에서 직전에 없었거나 `wall_distance_pct`% 이상 떨어진 벽은 `wall_is_suspect = True`로 표시하고 지지/저항으로 사용하지 않는다.
2. **아이스버그/숨겨진 주문 가드**: 거래소가 아이스버그 주문을 지원하면 가시적 깊이가 실제 유동성을 과소평가한다. OBI 신호 전체를 확률적으로 해석하고, 가능하면 체결된 거래 흐름(틱 데이터)으로 확인한다.
3. **넓은 스프레드 = 신뢰 불가 가드**: `spread_pct > spread_pct_threshold`이면 호가창이 얇아 OBI가 단일 체결만으로 급반전 가능. `spread_is_wide == False`를 모든 OBI 신호의 게이트 조건으로 사용한다.
4. **불균형 반전 가드**: OBI는 스냅샷 한 사이클 안에 `STRONG_BID`에서 `STRONG_ASK`로 뒤집힐 수 있다. 행동하기 전 연속 2스냅샷 이상 국면이 안정적임을 확인하는 `consecutive_regime_count` 가드(최소 2)를 추가한다.
5. **거래소 외부/다크풀 유동성 가드**: 크립토 거래량의 상당 부분은 OTC나 호가창에 보이지 않는 다크풀에서 거래된다. 단일 거래소(예: Binance 현물) OBI는 시장 전체 압력을 반영하지 않을 수 있다. 퍼페추얼 기준으로는 펀딩 비율과 미체결약정(OI)을 함께 확인한다.
6. **저유동성 알트코인 가드**: 1% 밴드 내 총 bid+ask 깊이가 BTC 등가 50개 미만인 종목은 OBI 노이즈가 신호를 압도한다 — 신호 생성 금지.
7. **세션/얇은 시장 시간대 가드**: 오프피크(주말, 아시아 비정규 시간대)에는 호가창이 구조적으로 얇아진다. 해당 시간대에는 `obi_strong_threshold`를 0.8로, `obi_zscore` 요구치를 2.0 이상으로 상향한다.
8. **±1.0 근접 극단 OBI 가드**: `|obi_weighted| > 0.9`는 반전 직전 소진 신호일 수 있다(특히 `obi_zscore > 3.0` 동반 시). 주의 플래그로 처리하되 확인된 반전 신호는 아님 — 경험적으로 평균 회귀를 앞서는 경우가 있으나 자산 및 국면에 따라 결과가 다르다.

---

**함정** —

- **단일 스냅샷 한계**: `fetch_order_book`은 정적 L2 스냅샷만 캡처한다. 스냅샷 사이의 빠른 주문 취소(스푸핑)는 연속 스냅샷을 비교하지 않는 한 감지 불가. 1Hz 미만 폴링은 신호 품질을 심각하게 저하시킨다.
- **ccxt timestamp None 주의**: 일부 거래소(초기 Binance 구현 등)는 호가창 딕셔너리의 `timestamp`에 `None`을 반환한다. `int(time.time() * 1000)`으로 로컬 타임스탬핑으로 폴백한다.
- **OBI는 거래소별 데이터**: Binance BTC/USDT OBI는 Coinbase, OKX, Bybit 깊이를 포함하지 않는다. 집계 호가창 분석은 거래소별 별도 호출과 거래량 정규화 가중치가 필요하다.
- **레벨 정렬 오류**: ccxt는 bid 내림차순, ask 오름차순을 보장하지만 일부 거래소 구현에서 역순 반환이 관찰되었다(ccxt issue #24859). `ob['bids'][0][0] > ob['bids'][1][0]` 검증을 진행 전에 반드시 수행한다.
- **깊이 레벨 수 불일치**: `ob_depth_levels=20`을 요청해도 얇은 호가창 거래소가 10레벨만 반환할 수 있다. Step 5에서처럼 항상 `N = min(obi_top_n, len(ob['bids']), len(ob['asks']))`를 사용한다.
- **VAMP vs Stoikov 미세 가격 혼동**: Step 4의 수식(VAMP, 상위 레벨만 사용)은 Stoikov 미세 가격이 아니다. Stoikov의 완전 미세 가격은 역사적 마르코프 체인과 6회 재귀가 필요하다. VAMP는 진입 타이밍 목적으로 충분하지만 하위 시스템에서 'micro-price'로 표기하면 혼동을 초래한다.
- **OBI 국면 임계값의 범용성 결여**: ±0.6/±0.2 5구간 분류는 Cartea et al. (2018)의 TDS 크립토 OBI 연구를 따른다 — Cont et al. 2014(OFI 정의)가 아님. 기본값은 합리적인 출발점이지만 자산별 역사적 OBI-수익률 분석을 통해 교정해야 한다.
- **OFI vs OBI 명명 혼동**: 'Order Flow Imbalance(OFI)'(Cont, Kukanov, Stoikov 2014)는 연속 틱 간 최우선 bid/ask 대기량의 누적 변화를 추적하는 동적 측정치다. 'Order Book Imbalance(OBI)'는 정적 스냅샷 비율이다. 이 스펙은 OBI를 구현한다. 실무 기사에서 두 개념이 혼용되는 경우가 많으니 하위 소비자가 구별하도록 주의한다.
- **z-score 표준편차 선택**: Step 11은 `statistics.stdev`(표본 표준편차, Bessel 보정, n-1 분모)를 사용한다. `statistics.pstdev`(모집단 표준편차, n 분모)를 사용하면 소규모 창에서 분산을 체계적으로 과소평가하여 z-score가 과대 추정된다 — 롤링 창은 진행 중인 스트림의 표본이므로 표본 표준편차가 올바르다.

---

**참고** —

- https://quantstrategy.io/blog/order-book-imbalances-a-practical-guide-for-day-traders/
- https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Order%20Book%20Imbalance.html
- https://questdb.com/blog/order-book-imbalance-analysis/
- https://towardsdatascience.com/price-impact-of-order-book-imbalance-in-cryptocurrency-markets-bf39695246f6/
- https://www.quantstart.com/articles/high-frequency-trading-ii-limit-order-book/
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694
- https://dm13450.github.io/2022/02/02/Order-Flow-Imbalance.html
- https://bookmap.com/knowledgebase/docs/KB-Indicators-Imbalance
- https://mmt.gg/learn/order-book-imbalance
- https://blog.adnansiddiqi.me/getting-started-with-ccxt-crypto-exchange-library-and-python/
- https://www.buildix.trade/blog/long-short-ratio-is-misleading-order-book-imbalance-better-2026
- https://arxiv.org/html/2504.15908v1
- https://ideas.repec.org/a/oup/jfinec/v12y2014i1p47-88..html
- https://arxiv.org/pdf/1907.06230
- https://medium.com/@mhfizt/high-frequency-estimator-of-future-prices-micro-price-paper-code-walkthrough-475adb98e91d
- https://www.blockchainresearchlab.org/wp-content/uploads/2019/07/Discovering-market-prices-Meyer-Fiedler-BRL-Series-No.-2.pdf

---

## 11. 미체결약정 (OI) (open_interest)

**정의** — Open Interest (OI, 미체결약정)는 아직 청산되지 않은 선물/퍼페추얼 계약의 총 수량이다. 계약 하나당 정확히 한 쪽 롱과 한 쪽 숏이 존재하므로, OI는 시장에 현재 살아 있는 자본의 총량을 나타낸다. OI가 증가한다는 것은 신규 포지션이 개설되어 자본이 유입되고 있음을 뜻하고, OI가 감소한다는 것은 기존 포지션이 자발적으로 또는 강제 청산으로 닫히고 있음을 뜻한다.

표준 해석은 4사분면 모델(4-quadrant model)을 사용한다. 가격 방향과 OI 방향을 교차하면 네 가지 국면이 나온다. (1) 가격 상승 + OI 상승 = 신규 롱이 랠리를 주도하는 강세 추세(BULL_TREND) — 지속 가능한 상승; (2) 가격 하락 + OI 상승 = 신규 숏이 하락을 주도하는 약세 추세(BEAR_TREND) — 지속 가능한 하락; (3) 가격 상승 + OI 하락 = 기존 숏이 환매(숏 커버링)하면서 가격을 밀어 올리는 약세 랠리(SHORT_COVER) — 신규 매수자 유입 없이 발생하므로 지속성 낮음; (4) 가격 하락 + OI 하락 = 기존 롱이 손절/청산되면서 가격을 끌어내리는 항복(capitulation) 국면(LONG_LIQ).

자금 조달 비율(funding rate)을 OI와 결합하면 스퀴즈(squeeze) 리스크를 식별할 수 있다. 양(+) 펀딩이 높고 OI가 상승 중이면 롱 포지션이 과밀한 상태(crowded long)로 연쇄 청산 위험이 있고, 음(-) 펀딩이 깊고 OI가 상승 중이면 숏이 과밀한 상태(crowded short)로 숏 스퀴즈 리스크가 있다. OI의 롤링 z-score는 자산 규모의 구조적 성장을 보정하여 통계적으로 극단적인 포지셔닝 구간을 식별한다.

---

**탐지 알고리즘** —

1. **데이터 수집 (Step 0)**
   - `bars`: `PriceBar` 리스트, `ts` 기준 오름차순 정렬. 각 항목에 `ts`(int ms), `open`, `high`, `low`, `close`, `volume`, `symbol` 포함.
   - `oi_records`: `ccxt.fetch_open_interest_history(symbol='BTC/USDT:USDT', timeframe='1h', since=<ms>, limit=500)` 결과. 각 레코드 키(ccxt PR #15088 이후): `timestamp`(int ms), `openInterestAmount`(float), `openInterestValue`(float), `symbol`, `datetime`, `info`. **필수 가드**: 호출 전 `exchange.has['fetchOpenInterestHistory']`를 확인하고 `False`이면 즉시 예외 발생. ccxt에서 스왑/선물 심볼(`BTC/USDT:USDT`)만 지원하며 현물 심볼(`BTC/USDT`) 호출 시 오류 발생.
   - `fr_records`: `ccxt.fetch_funding_rate_history(symbol='BTC/USDT:USDT', since=<ms>, limit=500)` 결과. 각 레코드 키: `timestamp`(int ms), `fundingRate`(float).

2. **펀딩 결제 주기 자동 감지 (Step 0.5)**
   `fr_records`에 항목이 2개 이상이면 연속 레코드 간격을 최대 5쌍 측정하고 `statistics.median`으로 `funding_period_ms`를 계산한다. `funding_period_hours = funding_period_ms / 3_600_000` (예: 1.0, 4.0, 8.0시간). 항목이 부족하면 `funding_period_hours = 8.0`으로 안전 폴백. 이 값을 이후 모든 연율화 계산에 사용한다. **중요**: Binance는 2025년 9월부터 고정 8시간 결제를 동적 1시간/4시간 결제로 변경했다(2026년 1월 업데이트 포함). 상수 1095(= 3 × 365)는 8h 계약에만 유효하며, 1h 계약은 8760을 사용한다.

3. **OI/펀딩 데이터를 가격 봉에 정렬 (Step 1)**
   각 봉 `b` (인덱스 `i`)에 대해 타임스탬프가 `b.ts` 이하인 OI 레코드 중 가장 늦은 것을 선택(forward-fill, 미래 데이터 유입 없음). `openInterestAmount`를 우선 사용하고, `None`이면 `openInterestValue`로 대체(교차 거래소 비교 가능성 위해 Amount 우선). 직전 봉 기준으로 `2 × bar_duration_ms`를 벗어난 레코드는 `None`으로 처리. 펀딩 레코드도 동일 방법으로 정렬해 `fr_aligned[i]`를 구성한다.

4. **봉별 OI 변화율 계산 (Step 2)**
   봉 `i >= 1`에서 `oi_aligned[i]`와 `oi_aligned[i-1]` 모두 `None`이 아니면:
   `oi_chg[i] = (oi_aligned[i] - oi_aligned[i-1]) / oi_aligned[i-1]` (분수 변화율).
   `oi_chg[0] = None` (직전 봉 없음).
   `oi_rising[i] = (oi_chg[i] is not None) and (oi_chg[i] > 0)`.
   `oi_falling[i] = (oi_chg[i] is not None) and (oi_chg[i] < 0)`.

5. **4사분면 분류 (Step 3)**
   봉 `i >= 1`에서 `oi_chg[i]`가 `None`이 아닐 때:
   - `price_up = bars[i].close > bars[i-1].close`
   - `price_dn = bars[i].close < bars[i-1].close`
   - `quadrant[i]`:
     - `BULL_TREND`: `price_up and oi_rising[i]` — 신규 롱 유입, 지속 가능한 랠리
     - `BEAR_TREND`: `price_dn and oi_rising[i]` — 신규 숏 유입, 지속 가능한 하락
     - `SHORT_COVER`: `price_up and oi_falling[i]` — 숏 포지션 청산(환매), 약세 랠리
     - `LONG_LIQ`: `price_dn and oi_falling[i]` — 롱 포지션 청산, 항복 국면
     - `NEUTRAL`: 그 외(가격 변동 없음 또는 OI 데이터 미비)

6. **롤링 OI z-score 계산 (Step 4)**
   창 크기 `oi_zscore_window`(기본 50봉). 봉 `i`에서:
   - `window_vals`: `range(max(0, i - oi_zscore_window + 1), i + 1)` 구간 중 `None`이 아닌 `oi_aligned` 값.
   - 유효 값이 `oi_zscore_window // 2` 미만이면 `oi_zscore[i] = None`.
   - `mean_oi = statistics.mean(window_vals)`, `stdev_oi = statistics.pstdev(window_vals)` (유한 창에 대한 모집단 표준편차).
   - `oi_zscore[i] = (oi_aligned[i] - mean_oi) / stdev_oi if stdev_oi > 0 else 0.0`.
   - `oi_extreme[i]`: `oi_zscore[i] is not None and abs(oi_zscore[i]) >= zscore_extreme_threshold` (기본 2.0, 정규분포 기준 상/하위 약 2.3%).
   - `oi_buildup_zone[i]`: `oi_zscore[i] is not None and abs(oi_zscore[i]) >= zscore_buildup_threshold` (기본 1.0, 약 84.1퍼센타일 이상).

7. **OI 축적 연속 봉 계산 (Step 5)**
   각 봉 `i >= 1`에서 역방향으로 탐색:
   - `streak = 0`
   - `j`를 `i`부터 `1`까지 역방향 순회: `oi_chg[j]`가 `None`이 아니고 `> 0`이면 `streak += 1`, 그렇지 않으면 break.
   - `oi_buildup_streak[i] = streak`.
   - `oi_buildup[i] = oi_buildup_streak[i] >= buildup_streak_bars` (기본 3봉).
   - **주의**: `oi_chg[0]`은 항상 `None`이므로 `j = 1`에서 루프를 멈춰도 안전하다 — `j = 0`은 즉시 스트리크를 끊는다.

8. **펀딩 비율 분류 (Step 6)**
   Step 0.5에서 감지한 `funding_period_hours`를 사용. `periods_per_year = (24.0 / funding_period_hours) * 365`.
   각 봉 `i`에서 `fr_aligned[i]`가 `None`이 아니면:
   - `funding_per_period = fr_aligned[i]` (예: `0.0001` = 기간당 0.01%).
   - `funding_annualized = funding_per_period * periods_per_year`.
     - 8h 결제 예시: `0.0001 × 1095 = 0.1095` (연 10.95% APR).
     - 1h 결제 예시: `0.0001 × 8760 = 0.876` (연 87.6% APR).
   - `fr_state[i]`:
     - `LONG_HEAVY`: `funding_per_period >= funding_extreme_threshold` (기본 0.0005, ≥+0.05%/기간)
     - `LONG_LEAN`: `funding_neutral_threshold <= funding_per_period < funding_extreme_threshold` (+0.01%~+0.05%/기간)
     - `NEUTRAL`: `abs(funding_per_period) < funding_neutral_threshold` (절댓값 < 0.01%/기간)
     - `SHORT_LEAN`: `-funding_extreme_threshold < funding_per_period <= -funding_neutral_threshold` (-0.05%~-0.01%/기간)
     - `SHORT_HEAVY`: `funding_per_period <= -funding_extreme_threshold` (<= -0.05%/기간)

9. **스퀴즈 리스크 감지 (Step 7)**
   봉 `i`에서 OI와 펀딩이 모두 유효할 때:
   - `long_squeeze_risk[i]`: `fr_state[i] in ('LONG_HEAVY', 'LONG_LEAN') AND oi_rising[i] AND oi_zscore[i] is not None AND oi_zscore[i] >= zscore_buildup_threshold` — 롱 과밀, 하락 연쇄 청산 전조.
   - `short_squeeze_risk[i]`: `fr_state[i] in ('SHORT_HEAVY', 'SHORT_LEAN') AND oi_rising[i] AND oi_zscore[i] is not None AND oi_zscore[i] >= zscore_buildup_threshold` — 숏 과밀, 상승 스퀴즈 전조.
   - **구분 핵심**: `short_squeeze_risk`는 스퀴즈 발생 이전의 셋업(pre-squeeze setup)이고, 활성 스퀴즈는 `quadrant == SHORT_COVER`(OI 하락 + 가격 상승)다. 두 신호를 혼용하면 반대 방향으로 진입하는 오류가 발생한다.
   - `long_squeeze_extreme[i]`: `fr_state[i] == 'LONG_HEAVY' AND oi_extreme[i]`.
   - `short_squeeze_extreme[i]`: `fr_state[i] == 'SHORT_HEAVY' AND oi_extreme[i]`.

10. **청산 캐스케이드 신호 (Step 8)**
    봉 `i >= 1`에서 `bars[i-1].close > 0`, `oi_aligned[i]`와 `oi_aligned[i-1]` 모두 `None`이 아닐 때:
    - `price_chg_pct = abs(bars[i].close - bars[i-1].close) / bars[i-1].close`.
    - `oi_drop_pct = (oi_aligned[i-1] - oi_aligned[i]) / oi_aligned[i-1]` (양수 = OI 감소).
    - `cascade_long[i]`: `price_chg_pct >= cascade_price_move_pct AND bars[i].close < bars[i-1].close AND oi_drop_pct >= cascade_oi_drop_pct` — 가격 급락 + OI 급감: 롱 강제 청산 진행 중.
    - `cascade_short[i]`: `price_chg_pct >= cascade_price_move_pct AND bars[i].close > bars[i-1].close AND oi_drop_pct >= cascade_oi_drop_pct` — 가격 급등 + OI 급감: 숏 강제 청산 진행 중.
    - 기본값: `cascade_price_move_pct = 0.02` (1h 기준; 일봉 ~0.08, 15m ~0.015로 타임프레임별 재보정 필수), `cascade_oi_drop_pct = 0.03`.
    - 누적 항복 감지: `window_oi = [oi_aligned[j] for j in range(max(0, i - oi_zscore_window + 1), i + 1) if oi_aligned[j] is not None]`. `oi_max_window = max(window_oi) if window_oi else None`. `oi_capitulation[i] = (oi_max_window is not None and oi_max_window > 0 and (oi_max_window - oi_aligned[i]) / oi_max_window >= capitulation_oi_drop_pct)` (기본 0.30, 창 고점 대비 30% 이상 감소).

11. **OI-가격 다이버전스 (Step 9)**
    창 `divergence_lookback`(기본 10봉). 유효 OI 값이 `divergence_lookback // 2` 미만이면 skip.
    - `price_window = [bars[j].close for j in range(max(0, i - lookback + 1), i + 1)]`.
    - `oi_window = [oi_aligned[j] for j in range(max(0, i - lookback + 1), i + 1) if oi_aligned[j] is not None]`.
    - `price_new_high = bars[i].close >= max(price_window)`.
    - `price_new_low = bars[i].close <= min(price_window)`.
    - `oi_new_high = oi_aligned[i] is not None and oi_aligned[i] >= max(oi_window)`.
    - `oi_new_low = oi_aligned[i] is not None and oi_aligned[i] <= min(oi_window)`.
    - **베어리시 다이버전스** (`bearish_div[i]`): `price_new_high AND NOT oi_new_high` — 가격은 N봉 고점 갱신, OI는 미확인(숏 커버링 주도 랠리, 신규 롱 부재, 지속성 의심).
    - **불리시 다이버전스** (`bullish_div[i]`): `price_new_low AND NOT oi_new_low` — 가격은 N봉 저점 갱신, OI는 N봉 저점 미갱신(신규 숏 진입 부재, 하락 소진 징후).
    - **추가 가드**: `statistics.pstdev(oi_window) == 0`(OI 값 전부 동일 = 데이터 정체)이면 두 다이버전스 모두 `False`.

12. **봉별 통합 신호 출력 (Step 10)**
    각 봉 `i`에 대해 결과 레코드를 생성한다. `strength` = 아래 10개 불리언 플래그 합산 / 10.0:
    `[oi_buildup, oi_extreme, long_squeeze_risk, short_squeeze_risk, long_squeeze_extreme, short_squeeze_extreme, cascade_long, cascade_short, bearish_div, bullish_div]`.
    **주의**: 플래그 수는 정확히 10개이므로 정규화 분모는 10이다(초기 초안의 8은 오류).

---

**파라미터** —

| name | default | 의미 |
|------|---------|------|
| `oi_zscore_window` | `50` | OI z-score 계산용 롤링 창 크기(봉 수). 평균·표준편차 분모. |
| `zscore_buildup_threshold` | `1.0` | OI 축적 구간 판단 z-score 임계값. 정규분포 기준 약 84.1퍼센타일 이상을 의미 있는 편차로 분류. |
| `zscore_extreme_threshold` | `2.0` | 극단적 과밀 포지셔닝 z-score 임계값. 정규분포 기준 상/하위 약 2.3%. |
| `buildup_streak_bars` | `3` | OI 축적 선언에 필요한 연속 상승 봉 수. 1h 봉 기준 3봉 = 3시간 연속 축적. |
| `funding_neutral_threshold` | `0.0001` | 중립 판단 기준 펀딩 비율(기간당). 0.01%/기간. 8h 결제 시 연 약 10.95% APR에 해당. |
| `funding_extreme_threshold` | `0.0005` | 극단적 과밀 판단 기준 펀딩 비율(기간당). 0.05%/기간. 8h 결제 시 연 약 54.75% APR에 해당. |
| `cascade_price_move_pct` | `0.02` | 캐스케이드 확인용 단일 봉 최소 가격 변동(분수). 1h 봉 기준 2%; 일봉 ~8%, 15m 봉 ~1.5%로 조정. |
| `cascade_oi_drop_pct` | `0.03` | 캐스케이드 확인용 단일 봉 최소 OI 감소율(분수). 1h 기준 3%. 타임프레임에 비례 조정. |
| `capitulation_oi_drop_pct` | `0.30` | 롤링 창 고점 대비 누적 OI 감소율 임계값. 30% 이상이면 대규모 강제 청산 국면으로 판단. |
| `divergence_lookback` | `10` | 다이버전스 탐지용 최근 봉 수. 최소 유효 OI 샘플 5개 필요. |

---

**출력 필드** —

| 필드 | 타입 | 설명 |
|------|------|------|
| `ts` | int/date | 봉 타임스탬프 |
| `symbol` | str | 심볼 (예: `BTC/USDT:USDT`) |
| `quadrant` | str | 4사분면: `BULL_TREND` / `BEAR_TREND` / `SHORT_COVER` / `LONG_LIQ` / `NEUTRAL` |
| `oi_value` | float\|None | 정렬된 OI 값(계약 수량 우선) |
| `oi_chg_pct` | float\|None | 전봉 대비 OI 변화율(분수) |
| `oi_zscore` | float\|None | 롤링 창 z-score |
| `oi_buildup` | bool | 연속 `buildup_streak_bars`봉 이상 OI 상승 여부 |
| `oi_buildup_streak` | int | 현재 연속 OI 상승 봉 수 |
| `oi_extreme` | bool | z-score ≥ `zscore_extreme_threshold` (극단 과밀) |
| `oi_capitulation` | bool | 창 고점 대비 30%+ 누적 OI 감소 (대규모 청산 국면) |
| `funding_rate` | float\|None | 정렬된 펀딩 비율(기간당) |
| `funding_period_hours` | float | 감지된 펀딩 결제 주기(시간 단위) |
| `funding_state` | str\|None | `LONG_HEAVY` / `LONG_LEAN` / `NEUTRAL` / `SHORT_LEAN` / `SHORT_HEAVY` |
| `long_squeeze_risk` | bool | 롱 과밀 + OI 상승: 하락 연쇄 청산 전조 |
| `short_squeeze_risk` | bool | 숏 과밀 + OI 상승: 상승 스퀴즈 전조 |
| `long_squeeze_extreme` | bool | `LONG_HEAVY` 펀딩 + 극단 OI: 고확신 롱 과밀 |
| `short_squeeze_extreme` | bool | `SHORT_HEAVY` 펀딩 + 극단 OI: 고확신 숏 과밀 |
| `cascade_long` | bool | 가격 급락 + OI 급감: 롱 강제 청산 진행 중 |
| `cascade_short` | bool | 가격 급등 + OI 급감: 숏 강제 청산 진행 중 |
| `bearish_div` | bool | 가격 N봉 고점 갱신, OI 미확인: 약세 다이버전스 |
| `bullish_div` | bool | 가격 N봉 저점 갱신, OI 미확인: 강세 다이버전스 |
| `direction` | str | 진입 방향 신호: `LONG` / `SHORT` / `WAIT` / `AVOID` / `NEUTRAL` |
| `strength` | float | 10개 플래그 합 / 10.0 (0~1) |

---

**진입 관련성** — `direction` 필드는 아래 우선순위 순서로 평가한다:

1. `cascade_long == True` → **`AVOID`**: 롱 강제 청산 진행 중. `cascade_long`이 연속 2봉 이상 `False`로 전환될 때까지 롱 진입 금지.
2. `cascade_short == True` → **`AVOID`**: 숏 강제 청산 진행 중. `cascade_short`이 연속 2봉 이상 `False`가 될 때까지 숏 진입 금지.
3. `short_squeeze_extreme == True` → **`LONG`**: 극단적 숏 과밀, 숏 스퀴즈 고확신 셋업. 군중 반대 방향 롱.
4. `long_squeeze_extreme == True` → **`SHORT`**: 극단적 롱 과밀, 연쇄 청산 고확신 셋업. 군중 반대 방향 숏.
5. `short_squeeze_risk == True` → **`LONG`**: 숏 과밀 전조 셋업. 롱 바이어스.
6. `long_squeeze_risk == True` → **`SHORT`**: 롱 과밀 전조 셋업. 숏 바이어스.
7. `quadrant == BULL_TREND AND NOT long_squeeze_risk` → **`LONG`**: 신규 롱 유입 확인, 과밀 없음.
8. `quadrant == BEAR_TREND AND NOT short_squeeze_risk` → **`SHORT`**: 신규 숏 유입 확인, 과밀 없음.
9. `quadrant == SHORT_COVER` → **`WAIT`**: 랠리가 신규 매수자 유입 없이 숏 환매로만 진행 중. OBI가 다시 상승으로 전환된 후 롱 진입 검토. (SHORT_COVER = 활성 스퀴즈 진행 중, short_squeeze_risk = 스퀴즈 발생 이전 셋업임을 혼동 금지.)
10. `quadrant == LONG_LIQ` → **`WAIT`**: 매도 소진 가능성. 숏 진입 금지, OI 안정화 대기.
11. `oi_capitulation == True` → **`WAIT`**: 대규모 청산 완료 국면. 잠재적 반전 구간으로 표시하되, 롱은 직전 저점 위 봉 마감, 숏은 직전 고점 아래 봉 마감 확인 후 진입.
12. 해당 없음 → **`NEUTRAL`**.

**타이밍 규칙**: `quadrant` 신호는 반드시 가격 구조 트리거(핵심 지지/저항 터치, 브레이크아웃 봉)와 동시에 발동될 때만 진입한다. 거래 방향과 같은 방향의 펀딩이 극단(`LONG_HEAVY`에서 롱, `SHORT_HEAVY`에서 숏)이면 진입 금지. **최강 진입 조건**: `quadrant in (BULL_TREND, BEAR_TREND) AND oi_buildup == True AND oi_extreme == False AND fr_state == NEUTRAL`.

---

**컨플루언스** —

| 방향 | 신호 조합 | 가중치 |
|------|-----------|--------|
| **강세 바이어스** | `quadrant == BULL_TREND`, `short_squeeze_risk`, `short_squeeze_extreme`, `bullish_div` | 0.70 (일반); 0.90 (`squeeze_extreme` 플래그) |
| **약세 바이어스** | `quadrant == BEAR_TREND`, `long_squeeze_risk`, `long_squeeze_extreme`, `bearish_div` | 0.70 (일반); 0.90 (`squeeze_extreme` 플래그) |
| **중립/회피** | `quadrant in (SHORT_COVER, LONG_LIQ)`, `cascade_long`, `cascade_short` 활성 | — |

OI는 암호화폐 선물 시장에 고유한 고품질 파생상품 신호로, 현물 분석에서는 사용 불가하다. 가격 액션 신호를 강력하게 업그레이드하거나 무력화한다. `squeeze_extreme` 플래그는 드물게 발생하지만 역사적으로 큰 임팩트를 가져 가중치 0.90을 부여한다. 단독 진입 신호로 사용하지 말고, 반드시 거래량 프로파일/VWAP(가격 구조 확인)와 청산 히트맵 데이터(가용 시)를 결합한다.

---

**거짓신호 가드** —

1. **SHORT_COVER 진입 가드**: `quadrant == SHORT_COVER`(가격 상승 + OI 하락)는 가격만 보면 강세처럼 보이지만 약세 랠리다. `buildup_streak_bars`봉 이내에 OI가 다시 상승으로 전환되지 않으면 롱 진입 skip.
2. **LONG_LIQ 진입 가드**: `quadrant == LONG_LIQ`는 항복 국면처럼 보이지만 추가 하락이 이어질 수 있다. `oi_capitulation == True` 확인 후 다음 봉에서 더 높은 저점 형성을 조건으로 롱 진입.
3. **단일 거래소 OI 편향 가드**: 단일 거래소 OI 데이터는 시장 전체 포지셔닝을 왜곡할 수 있다. 조회하는 거래소가 전체 퍼페추얼 OI의 20% 이상을 차지하는 심볼(Binance/Bybit의 BTC·ETH)에만 신호를 신뢰하고, 그 외는 `low_coverage = True`로 표시.
4. **만기일 OI 스파이크 가드**: 만기일이 있는 선물은 롤오버 시 OI가 급증하여 `BULL_TREND` 신호를 허위 발생시킨다. 만기 2일 이내에는 OI 신호 skip. 퍼페추얼은 덜 critical하나 단일 봉 `oi_chg_pct > 0.15`(15% 급증)는 점검.
5. **펀딩 단독 스퀴즈 신호 가드**: 펀딩 극단 상태만으로 스퀴즈 리스크를 활성화하지 않는다. `oi_rising[i] == True AND oi_zscore >= zscore_buildup_threshold` 조건을 반드시 동시에 만족해야 한다.
6. **단일 봉 캐스케이드 가드**: 대형 블록 단일 거래로 `cascade` 신호가 트리거될 수 있다. 연속 2봉 이상 캐스케이드 확인 또는 `bar.volume >= 2×롤링 평균 거래량` 조건 중 하나 충족 후 행동.
7. **다이버전스 거래량 확인 가드**: 다이버전스 창이 너무 짧으면 일반 조정에서도 `bearish_div`/`bullish_div`가 발생한다. 가격 신고점/저점과 동시에 `bar.volume`도 N봉 고점/저점을 기록해야 하며, 미충족 시 `NEUTRAL`로 다운그레이드.
8. **피드 정체 스트리크 가드**: `oi_chg[i] == 0.0`이 3봉 이상 연속이면 데이터 피드 정체(stale data) 가능성. `None`으로 처리하고 스트리크 카운트 초기화.

---

**함정** —

- **OI는 구조적으로 50% 롱 / 50% 숏**: OI 상승이 어느 쪽 주도인지 OI 자체로는 알 수 없다. 방향 판단의 타이브레이커는 펀딩 비율이다. 펀딩 양수이면 롱이 대가를 지불하는 쪽(롱 주도 포지셔닝), 음수이면 숏이 대가를 지불하는 쪽이다.
- **Amount vs Value 혼용 주의**: `openInterestAmount`(계약 수량)와 `openInterestValue`(USD 명목 금액)는 기초 자산 가격이 급등락할 때 큰 차이가 난다. 변화율 탐지에는 `Amount`를 사용한다(가격 움직임에 의한 가치 왜곡이 없음). 절대 규모 분류에만 `Value`를 사용한다.
- **ccxt 심볼 오류**: `ccxt.fetch_open_interest_history`는 스왑/선물 심볼(`BTC/USDT:USDT`)에서만 동작한다. 현물 심볼(`BTC/USDT`)에 호출하면 오류 발생. 모든 거래소가 이 API를 지원하지 않으므로 `exchange.has['fetchOpenInterestHistory']` 확인이 필수.
- **펀딩 결제 주기 변경**: Binance는 2025년 9월부터 고정 8시간 결제를 동적 1시간/4시간 결제로 변경했다(2026년 1월 업데이트). 과거 상수 `1095`(= 3 × 365)는 8h 계약에만 유효하다. 1h 계약은 `8760`(= 24 × 365). Step 0.5에서 실제 주기를 반드시 자동 감지해 사용한다.
- **SHORT_COVER와 short_squeeze_risk 혼동**: 일부 자료는 '숏 스퀴즈'를 OI 하락 + 가격 상승(숏이 강제 환매)으로 정의하고, 다른 자료는 OI 상승 + 음(-) 펀딩(숏 과밀 전조)으로 정의한다. 이 스펙은 두 신호를 분리 구현한다. `SHORT_COVER` = 활성 스퀴즈 진행 중, `short_squeeze_risk` = 스퀴즈 발생 이전 셋업. 두 신호를 동일한 의미로 혼용하면 잘못된 방향으로 진입한다.
- **롤링 z-score의 구조적 편향**: OI가 장기적으로 성장하는 자산(예: Binance BTC 퍼페추얼 OI가 2년간 $5B → $80B으로 증가)에서는 z-score가 지속적으로 양(+)으로 편향된다. 장기 타임프레임에서는 창을 넓히거나 1차 차분(first-differenced) OI를 z-score 입력으로 사용한다.
- **캐스케이드 임계값 타임프레임 의존성**: `cascade_oi_drop_pct = 3%`는 1h 봉 기준이다. 일봉에 그대로 적용하면 과다 탐지(false positive), 15m 봉에 적용하면 미탐(false negative)이 발생한다. 타임프레임 변경 시 반드시 재보정한다.
- **OI 데이터 지연**: 일부 거래소는 OI를 최대 15분 지연 제공한다. 15m 봉 이하에서는 최신 OI 봉이 가격 봉보다 1~2봉 뒤처질 수 있다. 정확히 일치하는 타임스탬프를 요구하지 말고, 1봉 정렬 허용 범위를 설정한다.
- **strength 정규화 오류**: 신호 플래그는 정확히 10개이므로 `strength` 분모는 **10**이다. 초기 초안의 8은 오류다.

---

**참고** —

- https://coinswitch.co/switch/crypto/what-is-open-interest-oi-in-crypto-trading/
- https://www.sharpe.ai/learn/futures-open-interest
- https://phemex.com/academy/open-interest-bitcoin-trading-2026
- https://medium.com/@cryptocreddy/comprehensive-guide-to-crypto-futures-indicators-f88d7da0c1b5
- https://tradelink.pro/blog/funding-rate-open-interest/
- https://quantjourney.substack.com/p/funding-rates-in-crypto-the-hidden
- https://web3.gate.com/crypto-wiki/article/how-do-futures-open-interest-and-funding-rates-signal-crypto-derivatives-market-trends-in-2026-20260202
- https://www.mexc.com/news/1032094
- https://www.tradingview.com/script/AjqADRsu-Open-Interest-Z-Score-BackQuant/
- https://github.com/ccxt/ccxt/pull/15088
- https://github.com/ccxt/ccxt/issues/17854
- https://bingx.com/en/learn/article/what-is-funding-rate-and-how-use-it-in-crypto-trading
- https://www.binance.com/en/support/announcement/detail/e4445d0389ce4defa6009021fcf6ee46
- https://www.binance.com/en/square/post/01-02-2026-binance-to-adjust-funding-rate-settlement-frequency-for-perpetual-contracts-34533405296522
- https://www.5paisa.com/finschool/course/options-scalping-course/open-interest-spurts-and-4-quadrants/
- https://zerodha.com/varsity/chapter/open-interest/
- https://gainium.io/tools/funding-rate-calculator
- https://invezz.com/news/2025/07/22/binance-resumes-4-hour-funding-rate-settlement-for-five-usdt-contracts/

## 진입 타이밍 컨플루언스 프레임워크

### 설계 원칙

이 프레임워크는 11개 개념 스펙에서 추출한 방향성 편향(directional bias)과 타이밍 신호(timing signal)를 세 계층으로 분리한 뒤 가중 투표로 통합하여, 최종 진입 결정을 **ENTER_NOW / WAIT_FOR_PULLBACK / SCALE_IN / AVOID** 네 가지 상태 중 하나로 출력한다. 각 계층은 독립적으로 평가되며, 상위 계층의 VETO는 하위 계층 점수와 무관하게 전체 결정을 고정한다.

- **HTF 계층 (Higher-Timeframe Bias Layer)**: 방향성과 구조적 맥락을 확립한다. 이 계층에서 하드 VETO가 발생하면 LTF/파생 계층에 관계없이 전체 결정이 AVOID로 고정된다.
- **LTF 계층 (Lower-Timeframe Trigger Layer)**: HTF 편향과 일치하는 방향에서 구체적 진입 타이밍을 제공한다. 트리거 미충족 시 WAIT_FOR_PULLBACK을 반환한다.
- **파생 조건 계층 (Derived Condition Layer)**: 호가창(OBI), 미체결약정(OI), 와이코프 페이즈 등 극히 단기적이거나 선물 전용인 신호를 최종 게이트 및 가중치 보정으로 사용한다.

---

### 계층 1: HTF 바이어스 구성

HTF 계층은 시장구조, 유동성 스윕+MSS, 볼륨프로파일 세 소스로 구성된다.

| 소스 | 기여 방향 | 기본 가중치 | 조건부 조정 |
|------|-----------|------------|-------------|
| 시장구조 (Market Structure) | `trend_bias` 방향 | 0.30 | internal_CHoCH+BOS 콤보 → 0.40; RANGING → 0 |
| 유동성 스윕 + MSS | SSL 스윕→불리시, BSL 스윕→베어리시 | 0.30 | MSS 없는 단독 스윕 → 0.10; ob_cluster=True → +0.10(상한 0.40); oi_spike=True → 추가 +0.05 |
| 볼륨프로파일 (매물대) | 프로파일 shape 기반 | 0.20 | 20세션 초과 → 0.10; VA 내부 D-shape → 0.10 |

**HTF 하드 VETO 조건 (점수 무관 전체 AVOID 강제)**

1. `trend_bias = RANGING` — HTF 점수를 0으로 강제하고 전체 결정을 WAIT_FOR_PULLBACK으로 전환. RANGING이 해소되어 BULLISH 또는 BEARISH로 전환되기 전까지 모든 방향성 진입 보류.
2. `swing_CHoCH`가 의도 방향과 반대로 발생 — 구조가 진입 thesis를 무효화했으므로 AVOID. 이 VETO는 점수와 무관하게 최우선 적용된다.

**HTF 게이트 규칙 (VETO 이하 조건)**

- 볼륨프로파일 `shape = 'D'`이고 현재가가 Value Area 내부에 있으면 매물대 신호를 중립(0.10)으로 하향.
- 볼륨프로파일 프로파일 신선도 > 20세션이면 가중치를 0.10으로 하향.
- 유동성 스윕 발생 후 3봉 내 `close`가 `pool_price` 반대편으로 복귀하지 않으면 `LIQUIDITY_RUN` 판정 → 반전 진입 금지(스윕 가중치 0으로 강제).

---

### 계층 2: LTF 진입 트리거 구성

HTF 편향이 확립된 후 LTF에서 다음 다섯 소스의 진입 트리거를 평가한다.

| 소스 | 기본 가중치 | 진입 트리거 조건 | 무효(가중치 = 0) 조건 |
|------|------------|-----------------|----------------------|
| FVG | STRONG 0.65 / NORMAL 0.45 / WEAK 0.25 | 가격이 zone 내부 진입 + LTF CHoCH 또는 BoS 확인 | `mitigated=True`; `mitigation_type='ce'`; `strength='weak'` 단독; FVG 형성 바와 동일 바 |
| 오더블록 (OB) | unmitigated+htf_conf 0.65 / 단독 0.45 / 브레이커 0.30 | `zone_mid` 터치(표준) 또는 Fib 0.62~0.79 OTE 진입(보수적) | `mitigated=True`; `visited=True` 재테스트 시 강도 −0.15; HTF 추세 역방향 |
| 캔들패턴 | 삼봉 0.45 / 쌍봉 0.35 / 단봉 0.25 / doji·spinning_top 0.15 | `strength=2` → 확인 바 종가 패턴 바 high/low 돌파 후 진입; `strength=3` → 즉시 진입 | `strength=1` → WAIT; `mitigated=True` 레벨 |
| 볼륨분석 | no_supply/no_demand confirmed 0.75 / VDU 브레이크아웃 0.65 / OBV 다이버전스만 0.45 | `no_supply_confirmed=True` + `cmf_signal='bullish'` → 다음 바(+1) 진입; VDU `zone_end=True` + 다음 바 `rvol='elevated'` | climax 바 직후 → WAIT(secondary test까지); `evr_label='absorption'` → 롱 AVOID |
| 차트패턴 | 브레이크아웃 후 0.65 / pre-breakout 0.40 | `ts_breakout` 설정 + `volume_ratio_at_breakout >= breakout_vol_ratio` 충족 | `ts_breakout=None`; `strength < 0.4`; `mitigated=True`; limp breakout (apex 75% 이후) |

**LTF 게이트 규칙**

1. `strength='weak'` FVG는 가중치 0.25 이하이며 단독 진입 트리거로 사용 불가. 반드시 OB 또는 볼륨분석과 병행해야 한다.
2. 캔들패턴 `strength=1` — EntryState = WAIT. 다음 바 확인 조건(불리시: `close > 패턴 바 high`; 베어리시: `close < 패턴 바 low`) 충족 전까지 진입 보류.
3. 차트패턴 `ts_breakout=None` — WAIT 상태. 콘솔리데이션 내부 진입 금지.
4. `visited=True` OB 재테스트 — 두 번째 재테스트는 첫 번째 대비 신뢰도 하락. 강도 점수 0.15 차감 후 재산출.
5. Inverted Hammer는 약한 신호(strength=1)로 단독 진입 금지. `close > 패턴 바 high` 확인 필수.

---

### 계층 3: 파생 조건 (게이트 및 가중치 보정)

파생 조건 계층은 LTF 점수 합산 후 최종 게이트로 적용된다. 이 계층의 신호는 독립적 진입 트리거로 사용하지 않는다.

| 소스 | 기본 가중치 | 강화 조건 | 약화 조건 |
|------|------------|-----------|-----------|
| 호가창 OBI | 0.25 | `regime=STRONG_BID`(롱) + `delta_vamp>0` + `spread_is_wide=False` + 의심 ask 벽 없음 | 고변동성 구간 → 0.10; `obi_zscore` ±1.0 이내 → WAIT |
| 미체결약정 OI | BULL_TREND 0.70 / squeeze_extreme 0.90 | `quadrant=BULL_TREND`, `oi_buildup=True`, `oi_extreme=False`, `fr_state=NEUTRAL` | `quadrant=SHORT_COVER` 또는 `LONG_LIQ` → WAIT; `fr_state=LONG_HEAVY` → 롱 AVOID |
| 와이코프 페이즈 | Phase D/E 0.75 | `phase_confidence >= 0.6`, Spring + Test 확인(롱), UTAD 후 복귀(숏) | Phase A/B → 0.0; `phase_confidence < 0.4` → 0.0; `failed_breakout=True` → 0.0 |

**파생 하드 AVOID 조건 (캐스케이드 보호)**

- `cascade_long=True` — 롱 진입 강제 AVOID. 연속 2바 이상 `cascade_long=False`로 복귀 전까지 유효.
- `cascade_short=True` — 숏 진입 강제 AVOID. 동일 조건.
- `fr_state=LONG_HEAVY` 상태에서 롱 진입 시도 — 포지션 과열(overcrowding). AVOID.
- `fr_state=SHORT_HEAVY` 상태에서 숏 진입 시도 — 동일 이유. AVOID.

---

### 컨플루언스 점수 계산 공식

각 신호 소스 `i`에 대해 방향 정렬 지시자 `d_i`를 정의한다.

```
d_i = +1   신호가 의도 방향과 일치
d_i =  0   중립 또는 해당 없음 (signal_active_i = 0 또는 데이터 없음)
d_i = -1   신호가 의도 방향과 반대 (감점)
```

가중 원점수:

```
raw_score = sum(w_i * d_i * signal_active_i  for all i)
```

`signal_active_i`는 해당 신호의 계산 가능 여부(0 또는 1)이며, `w_i`는 각 스펙의 confluence 권고값을 실제 상태에 맞게 조건부 조정한 적용 가중치다. 점수는 활성 신호 기준 최대 가능 원점수(`max_raw`)로 나눠 0~100으로 정규화한다.

```
confluence_score = max(0.0, (raw_score / max_raw) * 100)
```

단, `max_raw = 0`이면 `confluence_score = 0` (활성 신호 없음).

**기본 가중치 참조 테이블 (실제 적용 시 스펙 조건에 따라 조정)**

| 신호 | 기본 가중치 | 주요 하향 조건 | 주요 상향 조건 |
|------|------------|---------------|----------------|
| 시장구조 (HTF) | 0.30 | RANGING→0 | internal_CHoCH+BOS 콤보→0.40 |
| 유동성 스윕+MSS | 0.30 | 스윕 단독(MSS 없음)→0.10 | ob_cluster=True→+0.10; oi_spike→+0.05 |
| 볼륨프로파일 | 0.20 | 20세션 초과→0.10; VA 내부→0.10 | 신선(≤5세션), 명확한 P/b-shape 유지 |
| FVG | 0.45(NORMAL) | WEAK→0.25; 단독 WEAK→0 | STRONG+HTF OB 겹침→0.65 |
| 오더블록 | 0.45(단독) | 방문(visited)→0.30; HTF 역방향→0 | unmitigated+htf_conf+FVG 겹침→0.65 |
| 캔들패턴 | 0.35(쌍봉) | 단봉→0.25; doji→0.15 | 주요 레벨 근처(0.5% 이내)→+1등급 |
| 볼륨분석 | 0.65 | OBV 다이버전스만→0.45; climax 직후→0 | no_supply/no_demand confirmed→0.75 |
| 차트패턴 | 0.65(브레이크 후) | pre-breakout→0.40; strength<0.5→0.35 | 볼륨 spike 동반 유지 |
| 와이코프 | 0.75(Phase D/E) | Phase A/B→0; confidence<0.4→0 | Spring+Test 확인+OI 하락(크립토)→실효 상승 |
| OI | 0.70(BULL_TREND) | SHORT_COVER/LONG_LIQ→0; cascade→VETO | squeeze_extreme→0.90 |
| OBI | 0.25 | 고변동성→0.10; zscore ±1.0 이내→0 | STRONG_BID+delta_vamp>0+no wall→0.25 유지 |

**Python 레퍼런스 구현**

```python
def confluence_score(signals: dict[str, tuple[float, int]]) -> float:
    """
    signals: {name: (weight_applied, direction)}
    weight_applied: float >= 0, 조건부 조정이 적용된 실제 가중치
    direction: int in {-1, 0, 1}
    Returns: float in [0, 100]
    """
    raw = sum(w * d for w, d in signals.values() if w > 0)
    max_raw = sum(w for w, _ in signals.values() if w > 0)
    if max_raw == 0:
        return 0.0
    return max(0.0, (raw / max_raw) * 100)
```

---

### 결정 임계값 및 무효화 레벨

| 점수 구간 | 결정 상태 | 의미 및 행동 |
|-----------|-----------|--------------|
| 70 이상 | **ENTER_NOW** | 복수 신호 강하게 정렬. 즉시 전체 포지션 진입 허용 |
| 50 ~ 69 | **SCALE_IN** | 신호 부분 정렬. 계획 포지션의 1/3~1/2 초기 진입 후 추가 확인 시 나머지 추가 |
| 35 ~ 49 | **WAIT_FOR_PULLBACK** | 방향성 형성 중이나 트리거 미충족. 진입 조건 충족 바 대기 |
| 35 미만 | **AVOID** | 신호 약하거나 혼재. 관망 |

**하드 VETO 조건 (점수와 무관하게 AVOID 강제)**

아래 조건 중 하나라도 해당하면 컨플루언스 점수 계산 결과와 관계없이 즉시 AVOID로 고정된다.

1. `swing_CHoCH`가 의도 방향과 반대로 발생 — 구조적 thesis 무효화
2. `cascade_long=True` (롱 시도 시) 또는 `cascade_short=True` (숏 시도 시)
3. `fr_state=LONG_HEAVY` (롱 시도 시) 또는 `fr_state=SHORT_HEAVY` (숏 시도 시)
4. HTF `trend_bias=RANGING`

**무효화(Invalidation) 레벨 정의**

무효화 레벨은 진입 근거가 소멸하는 가격이다. 해당 레벨의 종가 확정 시 포지션을 청산한다.

| 결정 상태 및 근거 | 무효화 레벨 |
|-----------------|------------|
| ENTER_NOW (롱, 유동성 스윕 기반) | SSL 스윕 극단 아래 − 0.5 × ATR(14) |
| ENTER_NOW (롱, OB 기반) | `mitigation_extreme` 아래 − 0.5 × ATR(14) |
| ENTER_NOW (롱, 와이코프 Spring 기반) | `spring_low` 아래 |
| ENTER_NOW (숏, 유동성 스윕 기반) | BSL 스윕 극단 위 + 0.5 × ATR(14) |
| ENTER_NOW (숏, OB 브레이커 기반) | 브레이커 `mitigation_extreme` 위 + 0.5 × ATR(14) |
| SCALE_IN (BUEC/LPS 기반, 와이코프) | TR_high × 0.97 아래 (범위 재진입 = 무효) |
| WAIT_FOR_PULLBACK (불리시) | HTF 스윙 저점(HL) 종가 하향 돌파 |
| WAIT_FOR_PULLBACK (베어리시) | HTF 스윙 고점(LH) 종가 상향 돌파 |

---

### 워크드 예시 1: BTC/USDT 퍼페추얼 4h 크립토 롱

**시나리오**

BTC/USDT 4h 차트. 일봉(HTF)에서 상승 시장구조(BULLISH), 최근 internal_CHoCH + internal_BOS 콤보 확인. 현재가가 일봉 swing 레인지의 디스카운트 구간(전체 레인지 50% 미만). 4h에서 SSL(swing low) 스윕 발생 후 MSS(4h CHoCH) 확인. 볼륨프로파일 P-shape, 현재가가 VAL 반등 구간. 15m FVG(STRONG, 불리시, HTF OB와 겹침, 미티게이션 없음) zone 내부로 가격 진입 및 15m BoS 발생. 강세 Engulfing 캔들 완성(strength=2), 확인 바 `close > 패턴 바 high` 충족. `no_supply_confirmed=True`, `cmf_signal='bullish'`, `rvol='elevated'`. OI `quadrant=BULL_TREND`, `oi_buildup=True`, `oi_extreme=False`, `fr_state=NEUTRAL`, `cascade_long=False`. OBI `regime=STRONG_BID`, `delta_vamp>0`, `spread_is_wide=False`, 의심 ask 벽 없음.

**하드 VETO 체크**: `swing_CHoCH` 반대 없음, cascade 없음, fr_state=NEUTRAL, RANGING 아님 → VETO 없음.

**점수 계산**

| 신호 | 적용 가중치 | 방향(d) | 기여 (w×d) | 비고 |
|------|------------|---------|-----------|------|
| 시장구조 HTF (internal_CHoCH+BOS 콤보) | 0.40 | +1 | +0.40 | 콤보 → 0.40 상향 |
| 유동성 스윕 + MSS 확인 | 0.30 | +1 | +0.30 | MSS 확인, ob_cluster 미적용 |
| 볼륨프로파일 (P-shape, VAL 반등, 신선) | 0.20 | +1 | +0.20 | 5세션 이내 신선 프로파일 |
| FVG (STRONG, HTF OB 겹침) | 0.65 | +1 | +0.65 | STRONG + HTF 겹침 최대값 |
| 오더블록 (unmitigated, htf_conf=True) | 0.65 | +1 | +0.65 | FVG 겹침 +0.10이나 상한 0.65 적용 |
| 캔들패턴 (Bullish Engulfing, strength=2, 확인 완료) | 0.35 | +1 | +0.35 | 쌍봉 기본값 |
| 볼륨분석 (no_supply_confirmed, CMF 불리시) | 0.75 | +1 | +0.75 | confirmed → 0.75 |
| OI (BULL_TREND, oi_buildup, fr_state=NEUTRAL) | 0.70 | +1 | +0.70 | squeeze_extreme 아님 → 0.70 |
| OBI (STRONG_BID, delta_vamp>0, 벽 없음) | 0.25 | +1 | +0.25 | 기본 상한 |

```
raw_score  = 0.40 + 0.30 + 0.20 + 0.65 + 0.65 + 0.35 + 0.75 + 0.70 + 0.25 = 4.25
max_raw    = 0.40 + 0.30 + 0.20 + 0.65 + 0.65 + 0.35 + 0.75 + 0.70 + 0.25 = 4.25
confluence = (4.25 / 4.25) × 100 = 100.0
```

활성 신호 9개 전부 정렬된 이상적 케이스. 실전에서는 와이코프·차트패턴이 미적용이므로 `max_raw`에서 제외된다. 점수 100은 상한 도달 의미이며 실무 판단 기준은 임계값(70) 초과 여부다.

**결정: ENTER_NOW**

- 진입가: 15m FVG `zone_mid`
- 스탑: SSL 스윕 극단 − 0.5 × ATR(14)
- 1차 목표: HTF `range_high`
- 2차 목표: Fib −0.62 extension
- 무효화: 스탑 레벨 종가 하향 확정 → 전량 청산

---

### 워크드 예시 2: 코스피 대형주 일봉 롱

**시나리오**

AQR 모멘텀 랭킹 상위 10% 편입 종목. 일봉 장기 상승구조 유지. 볼륨프로파일에서 전 주요 VAH(Value Area High)를 2일 연속 종가 돌파 후 현재 되돌림 중. 거래량 급감(dry-up, VDU zone_end 아직 미확정). OB zone(VAH 레벨)에 아직 미진입. 당일 캔들: Spinning Top(strength=1, 방향 미결). OI/OBI 데이터 없음(현물 주식). 유동성 스윕 이벤트 없음. MSS 미확인.

**하드 VETO 체크**: cascade 없음, RANGING 아님, swing_CHoCH 반대 없음 → VETO 없음.

**점수 계산 (현재 시점)**

| 신호 | 적용 가중치 | 방향(d) | 기여 (w×d) | 비고 |
|------|------------|---------|-----------|------|
| 시장구조 HTF (BULLISH) | 0.30 | +1 | +0.30 | 일봉 상승구조 유지 |
| 유동성 스윕 + MSS | 0.10 | 0 | 0 | 스윕 이벤트 없음, MSS 미확인 |
| 볼륨프로파일 (VAH 브레이크 후 되돌림) | 0.20 | +1 | +0.20 | VAH 지지 기대, 신선 프로파일 |
| FVG | 0 | 0 | 0 | FVG 없음, signal_active=0 |
| 오더블록 (VAH 레벨 OB, 아직 미진입) | 0.45 | 0 | 0 | zone 미진입 → 트리거 미발생, d=0 |
| 캔들패턴 (Spinning Top, strength=1) | 0.15 | 0 | 0 | WAIT 상태, 방향 미결 |
| 볼륨분석 (dry-up 진행, VDU zone_end 대기) | 0.45 | 0 | 0 | zone_end 미확정, 트리거 미발생 |
| 와이코프 (페이즈 미식별) | 0 | 0 | 0 | 스키마틱 미감지, signal_active=0 |
| OI / OBI (주식, 데이터 없음) | 0 | 0 | 0 | signal_active=0 |

```
raw_score  = 0.30 + 0 + 0.20 + 0 + 0 + 0 + 0 + 0 + 0 = 0.50
max_raw    = 0.30 + 0.10 + 0.20 + 0.45 + 0.45 + 0.15 + 0.45 = 2.10
confluence = (0.50 / 2.10) × 100 ≈ 23.8
```

**결정: AVOID** (23.8 < 35)

현재 시점에서는 HTF 방향성은 있으나 LTF 트리거가 전혀 충족되지 않았다. 다음 두 조건 충족 시 점수를 재계산하여 상태 전환을 평가한다.

**재계산 시나리오 (조건 충족 후)**

다음 바에서 (A) 가격이 OB zone(VAH) 내부로 진입하고 (B) VDU `zone_end=True` + 다음 바 `rvol='elevated'` + `close_loc='upper'` 동시 충족 시:

| 신호 | 적용 가중치 | 방향(d) | 기여 (w×d) |
|------|------------|---------|-----------|
| 시장구조 HTF | 0.30 | +1 | +0.30 |
| 유동성 스윕 + MSS | 0.10 | 0 | 0 |
| 볼륨프로파일 | 0.20 | +1 | +0.20 |
| 오더블록 (zone 진입, d 활성화) | 0.45 | +1 | +0.45 |
| 볼륨분석 (VDU 브레이크아웃 확인) | 0.65 | +1 | +0.65 |
| 캔들패턴 (확인 바 close > 패턴 high) | 0.35 | +1 | +0.35 |

```
raw_score  = 0.30 + 0 + 0.20 + 0.45 + 0.65 + 0.35 = 1.95
max_raw    = 0.30 + 0.10 + 0.20 + 0.45 + 0.45 + 0.35 + 0.65 = 2.50
confluence = (1.95 / 2.50) × 100 = 78.0
```

조건 충족 시 → 점수 78.0 ≥ 70 → **ENTER_NOW** 전환.

- 진입가: OB `zone_mid` (VAH 레벨)
- 스탑: VAH 아래 − 0.5 × ATR(14) 종가 확정
- 무효화: VAH 아래 종가 1봉 확정 → 진입 근거 소멸

**현재 행동**: OB zone 진입 + VDU 브레이크아웃 바 + 확인 캔들 대기 → **WAIT_FOR_PULLBACK** 유지.

---

## 구현 모듈 매핑

### 모듈 경로 및 핵심 함수

아래 경로는 `trader/engine/chart/` 기준 상대 경로다. 애그리게이터(`read.py`)는 나머지 11개 모듈을 import하여 신호 맵을 구성한 뒤 컨플루언스 점수와 최종 `EntryState`를 반환한다. 모든 모듈은 `PriceBar` 리스트를 표준 입력으로 받으며 pandas/numpy/TA-Lib 없이 stdlib(`statistics`, `math`)만 사용한다.

| 개념 | 모듈 경로 | 핵심 함수 |
|------|-----------|-----------|
| 시장구조 | `chart/structure.py` | `detect_swing_points(bars: list[PriceBar], lookback: int) -> list[SwingPoint]`; `classify_structure_events(bars, swings) -> list[StructureEvent]`; `compute_trend_bias(events: list[StructureEvent]) -> TrendBias`; `detect_eqh_eql(bars, swings) -> list[LiquidityLevel]`; `get_internal_structure(bars, swing_range: tuple[int,int]) -> list[StructureEvent]` |
| 공정가치 갭 (FVG) | `chart/fvg.py` | `detect_fvg(bars: list[PriceBar]) -> list[FVGZone]`; `classify_fvg_strength(zone: FVGZone, atr: float) -> FVGStrength`; `check_fvg_mitigation(zone: FVGZone, bar: PriceBar) -> MitigationType`; `is_price_in_zone(price: float, zone: FVGZone) -> bool`; `detect_ifvg(bars, fvg_zones: list[FVGZone]) -> list[IFVGZone]` |
| 오더블록 | `chart/order_block.py` | `detect_order_blocks(bars: list[PriceBar], events: list[StructureEvent]) -> list[OrderBlock]`; `score_order_block(ob: OrderBlock, htf_bias: TrendBias, oi_context: str | None) -> float`; `check_ob_mitigation(ob: OrderBlock, bar: PriceBar, atr: float) -> bool`; `get_ote_entry_range(ob: OrderBlock, swing_origin: float, displacement_peak: float) -> tuple[float, float]`; `detect_breaker_block(ob: OrderBlock, bars) -> BreakerBlock | None` |
| 유동성 (풀/스윕/OTE) | `chart/liquidity.py` | `identify_liquidity_pools(bars: list[PriceBar], levels: list[LiquidityLevel]) -> list[LiquidityPool]`; `detect_liquidity_sweep(bars, pools: list[LiquidityPool]) -> list[SweepEvent]`; `classify_sweep_type(sweep: SweepEvent, bars, oi_data=None) -> SweepType`; `detect_mss(bars, sweep: SweepEvent) -> MSSResult`; `compute_ote_zone(swing_low: float, swing_high: float, direction: str) -> OTEZone`; `classify_premium_discount(price: float, swing_low: float, swing_high: float) -> PriceZone`; `check_liquidity_run(bars, sweep: SweepEvent, window: int = 3) -> bool` |
| 볼륨프로파일 (매물대) | `chart/volume_profile.py` | `build_volume_profile(bars: list[PriceBar], n_bins: int = 50) -> VolumeProfile`; `classify_profile_shape(vp: VolumeProfile) -> ProfileShape`; `find_poc(vp: VolumeProfile) -> float`; `find_value_area(vp: VolumeProfile, pct: float = 0.70) -> ValueArea`; `find_naked_poc(vp: VolumeProfile, current_price: float, atr: float) -> list[float]`; `classify_lvn_hvn(vp: VolumeProfile, mean_factor: float = 2.0) -> list[VolumeNode]`; `get_profile_age(vp: VolumeProfile, current_bar: PriceBar) -> int` |
| 볼륨분석 | `chart/volume.py` | `compute_rvol(bars: list[PriceBar], lookback: int = 20) -> list[float]`; `classify_rvol(rvol: float, climax_mult: float = 2.0, dry_mult: float = 0.5) -> RVolClass`; `detect_climax(bar: PriceBar, rvol: float, spread: float) -> ClimaxType`; `detect_no_supply(bars: list[PriceBar], lookback: int = 3) -> NoSupplyResult`; `detect_no_demand(bars: list[PriceBar], lookback: int = 3) -> NoDemandResult`; `detect_vdu(bars: list[PriceBar], min_bars: int = 5) -> VDUResult`; `compute_obv(bars: list[PriceBar]) -> list[float]`; `detect_obv_divergence(bars, obv: list[float], pivot_window: int = 5) -> DivergenceSignal`; `compute_cmf(bars: list[PriceBar], period: int = 20) -> list[float]`; `compute_evr(bars: list[PriceBar]) -> list[EVRLabel]` |
| 와이코프 매집/분산 | `chart/wyckoff.py` | `detect_wyckoff_schematic(bars: list[PriceBar], oi_data=None) -> WyckoffSchematic | None`; `classify_phase(schematic: WyckoffSchematic, bar_idx: int) -> WyckoffPhase`; `score_phase_confidence(schematic: WyckoffSchematic) -> float`; `detect_spring(bars, schematic: WyckoffSchematic) -> SpringResult | None`; `detect_utad(bars, schematic: WyckoffSchematic) -> UTADResult | None`; `get_wyckoff_entry_signal(schematic, phase: WyckoffPhase, bar: PriceBar, atr: float) -> WyckoffEntrySignal` |
| 차트패턴 | `chart/patterns.py` | `detect_chart_patterns(bars: list[PriceBar], min_swing_bars: int = 5) -> list[ChartPattern]`; `score_pattern_strength(pattern: ChartPattern, volume_ratio: float) -> float`; `check_breakout(pattern: ChartPattern, bar: PriceBar, volume_ratio: float) -> BreakoutResult`; `classify_pattern_direction(pattern: ChartPattern) -> str`; `detect_pullback_retest(pattern: ChartPattern, bars, atr: float) -> RetestResult | None`; `is_limp_breakout(pattern: ChartPattern, breakout_bar_idx: int) -> bool` |
| 캔들패턴 | `chart/candles.py` | `detect_candlestick_patterns(bars: list[PriceBar]) -> list[CandlePattern]`; `classify_candle_strength(pattern: CandlePattern) -> int`; `check_confirmation(pattern: CandlePattern, next_bar: PriceBar) -> bool`; `is_pattern_at_key_level(pattern: CandlePattern, levels: list[float], atr: float, tol_pct: float = 0.005) -> bool`; `get_candle_entry_state(pattern: CandlePattern, confirmed: bool) -> EntryState` |
| 호가창 (L2 OBI) | `chart/orderbook.py` | `compute_obi_weighted(bids: list[list[float]], asks: list[list[float]], depth_band_pct: float = 0.02) -> float`; `classify_obi_regime(obi_weighted: float, threshold: float = 0.60) -> OBIRegime`; `compute_obi_zscore(obi_series: list[float], lookback: int = 20) -> float`; `detect_order_walls(bids, asks, mid_price: float, wall_ratio: float = 3.0) -> list[OrderWall]`; `compute_delta_vamp(bids: list[list[float]], asks: list[list[float]]) -> float`; `is_spread_wide(bids, asks, mid_price: float, max_spread_pct: float = 0.001) -> bool` |
| 미체결약정 (OI) | `chart/open_interest.py` | `classify_oi_quadrant(price_pct_change: float, oi_pct_change: float) -> OIQuadrant`; `compute_oi_zscore(oi_series: list[float], lookback: int = 20) -> float`; `detect_squeeze_risk(oi_zscore: float, funding_rate: float, direction: str) -> SqueezeRisk`; `detect_cascade_liquidation(oi_changes: list[float], bars: list[PriceBar], threshold: float = 0.05) -> CascadeResult`; `classify_funding_state(funding_rate: float, long_threshold: float = 0.01, short_threshold: float = -0.01) -> FundingState`; `score_oi_signal(quadrant: OIQuadrant, squeeze: SqueezeRisk, cascade: CascadeResult, funding: FundingState) -> OIScore` |
| 컨플루언스 애그리게이터 | `chart/read.py` | `build_signal_map(bars: list[PriceBar], htf_bars: list[PriceBar], direction: str, oi_data=None, orderbook=None) -> dict[str, tuple[float, int]]`; `compute_confluence_score(signals: dict[str, tuple[float, int]]) -> float`; `apply_hard_veto(structure: MarketStructure, oi_score: OIScore, funding: FundingState) -> bool`; `decide_entry_state(score: float, veto: bool) -> EntryState`; `get_invalidation_level(entry_state: EntryState, ctx: EntryContext) -> float`; `run_confluence(bars, htf_bars, direction, oi_data=None, orderbook=None) -> ConfluentResult` |

### 공유 타입 계약 (`chart/types.py`)

모듈 간 공유 타입은 단일 파일로 관리한다. 순환 임포트를 방지하기 위해 각 모듈은 `chart/types.py`만 import하고 다른 모듈을 직접 import하지 않는다.

```python
from dataclasses import dataclass, field
from enum import Enum
from datetime import date

# ── 공통 입력 ──────────────────────────────────────────────────
@dataclass
class PriceBar:
    symbol: str
    market: str
    ts: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    freq: str       # '1d', '4h', '1h', '15m' 등
    currency: str
    source: str

# ── 결정 상태 ──────────────────────────────────────────────────
class EntryState(Enum):
    ENTER_NOW        = "ENTER_NOW"
    SCALE_IN         = "SCALE_IN"
    WAIT_FOR_PULLBACK = "WAIT_FOR_PULLBACK"
    AVOID            = "AVOID"

# ── 시장구조 ──────────────────────────────────────────────────
class TrendBias(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"

class StructureEventType(Enum):
    SWING_BOS    = "SWING_BOS"
    SWING_CHOCH  = "SWING_CHOCH"
    INTERNAL_BOS = "INTERNAL_BOS"
    INTERNAL_CHOCH = "INTERNAL_CHOCH"

@dataclass
class StructureEvent:
    event_type: StructureEventType
    ts: date
    price: float
    direction: str   # 'bullish' | 'bearish'

@dataclass
class MarketStructure:
    trend_bias: TrendBias
    events: list[StructureEvent] = field(default_factory=list)

# ── OI / OBI ──────────────────────────────────────────────────
class OIQuadrant(Enum):
    BULL_TREND  = "BULL_TREND"
    BEAR_TREND  = "BEAR_TREND"
    SHORT_COVER = "SHORT_COVER"
    LONG_LIQ    = "LONG_LIQ"

class OBIRegime(Enum):
    STRONG_BID = "STRONG_BID"
    STRONG_ASK = "STRONG_ASK"
    NEUTRAL    = "NEUTRAL"

class FundingState(Enum):
    LONG_HEAVY  = "LONG_HEAVY"
    SHORT_HEAVY = "SHORT_HEAVY"
    NEUTRAL     = "NEUTRAL"

@dataclass
class OIScore:
    quadrant: OIQuadrant
    squeeze_risk: bool
    squeeze_extreme: bool
    cascade_long: bool
    cascade_short: bool
    weight: float   # 0~0.90

# ── 진입 컨텍스트 (무효화 레벨 계산용) ─────────────────────────
@dataclass
class EntryContext:
    direction: str              # 'long' | 'short'
    sweep_extreme: float        # 유동성 스윕 극단가
    ob_mitigation_extreme: float
    spring_low: float
    atr14: float
    tr_high: float              # 와이코프 TR 상단

# ── 최종 출력 ──────────────────────────────────────────────────
@dataclass
class ConfluentResult:
    entry_state: EntryState
    confluence_score: float     # 0~100
    invalidation_level: float
    signal_map: dict[str, tuple[float, int]]  # {name: (weight, direction)}
    veto_triggered: bool
    veto_reason: str            # 빈 문자열이면 VETO 없음
```

### 신호 흐름 다이어그램 (텍스트)

```
PriceBar list (HTF + LTF)
        │
        ├── structure.py      → MarketStructure, TrendBias, StructureEvents
        ├── liquidity.py      → SweepEvents, MSSResult, OTEZone, PriceZone
        ├── volume_profile.py → VolumeProfile, ProfileShape, POC, VAH/VAL
        │       ↓
        │   [HTF Bias Layer — 점수 합산]
        │
        ├── fvg.py            → FVGZone list, IFVGZone list, MitigationType
        ├── order_block.py    → OrderBlock list, BreakerBlock list, OB score
        ├── candles.py        → CandlePattern list, confirmation state
        ├── volume.py         → NoSupplyResult, NoDemandResult, VDUResult, OBV div
        ├── patterns.py       → ChartPattern list, BreakoutResult
        │       ↓
        │   [LTF Trigger Layer — 점수 합산]
        │
        ├── orderbook.py      → OBIRegime, delta_vamp, OrderWall list
        ├── open_interest.py  → OIQuadrant, SqueezeRisk, CascadeResult, OIScore
        ├── wyckoff.py        → WyckoffPhase, phase_confidence, WyckoffEntrySignal
        │       ↓
        │   [Derived Condition Layer — 게이트 및 가중치 보정]
        │
        └── read.py (애그리게이터)
                build_signal_map()       → dict[str, tuple[float, int]]
                compute_confluence_score() → float (0~100)
                apply_hard_veto()        → bool
                decide_entry_state()     → EntryState
                get_invalidation_level() → float
                run_confluence()         → ConfluentResult
```

