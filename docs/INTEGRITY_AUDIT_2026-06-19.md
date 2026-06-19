# 배포후보 무결성 재감사 (2026-06-19)

> 계기: Biberion 샷건 백테스트에서 codex가 4대 버그(Sharpe 0.17→2.41, 14배 인플레)를 적발 →
> "돈 들어갈 배포후보(IDEAL 메가캡모멘텀+AQR, 결합 IDEAL+저변동성)도 같은 결함이 있나?" 검증.
> 방법: 4차원 적대 감사 워크플로 + 핵심 엔진 코드 직접 검증.

---

## 결론 요약

**두 레이어를 분리해야 한다:**

1. **기계적 백테스트 무결성 = CLEAN (코드 검증 완료).** 샷건의 4대 버그는 프로덕션 엔진에 **없다**. 그 버그들은 급조한 standalone `shotgun_walkforward.py`에만 있었고, `engine/factor_portfolio.py`는 제대로 지어졌다.
2. **엣지의 신뢰성 = 조건부 NO.** "+7.67%/yr OOS"라는 **주장**이 과장됐다(forward 표본 0, PBO FRAGILE, 생존편향이 크기에 잔존, US 전용). 엣지는 "real-but-modest, 방향 robust, 크기 fragile" — 프로젝트 메모리 결론과 일치.

---

## 1. 기계적 무결성 — CLEAN (샷건 4대 버그 대조, `engine/factor_portfolio.py` 코드 검증)

| 샷건 버그 | 배포후보 엔진 | 증거 |
|---|---|---|
| ① same-bar 체결 룩어헤드 | **없음** | `factor_portfolio.py:293-295,421-427` — `today`/`tomorrow` 분리, `_weighted_return(..., today, tomorrow)`: 가중치 결정=today, 수익 실현=today→tomorrow (forward) |
| ② 룩어헤드 스무딩 | **없음** | `:428,434,437` — `portfolio_return=gross_return-cost`, `equity*=1+portfolio_return`, 기간수익 직접 복리. 총수익을 보유 이전일에 분배하지 않음 |
| ③ 동시성 체리피킹 | **N/A** | 슬롯 기반 바스켓이 아니라 가중치 합≤1 포트폴리오 — 캡 체리피킹 구조 자체가 없음 |
| ④ 생존편향(현재구성) | **엔진은 PIT 지원** | `:335` `_eligible_symbols(membership_by_symbol, today)` = start/end_date 반영 PIT 멤버십 / `:421` `delistings_by_symbol` 적용 / `:723` `_fundamental_as_of(..., today)` = 발표일 이후만(펀더멘털 룩어헤드 없음) |
| (추가) fee | 적용됨 | `:416` `_turnover(current, target)*fee_bps/10_000` |
| (추가) maxDD/벤치 | forward | `:429-435` 벤치도 today→tomorrow |

→ **샷건의 14배 인플레는 이 엔진에서 재현 불가.** 펀더멘털 PIT(as_of), 멤버십 PIT, 상폐수익 적용, turnover 비용까지 정석.

## 2. 엣지 신뢰성 — 조건부 NO (4차원 감사 통합, 기존 검증 리포트 기반)

> 코드 감사 에이전트 4종은 StructuredOutput 반환 실패(서브에이전트 이슈) → 통합 에이전트가 기존 산출물 직접 판독.

- **[CRITICAL→해소중] "+7.67%/yr OOS" 라벨 과장.** 두 가지 정정:
  - (a) **수치**: +7.67%/93.3%은 비재현 registry 레코드 → 이미 **+8.15%/86.7%**(재현 핀)로 supersede됨(DEPLOYMENT_READINESS.md). **메모리만 stale했어서 2026-06-19 동기화 완료**(project_jaemu_trader·aqr_oos_ledger·MEMORY).
  - (b) **forward 라벨**: 이건 **walk-forward OOS(train/test 분할)지 live-forward 아님**. `paper-oos-ledger`에 실현 forward 수익 0건. **단 이는 결함이 아니라 시간 미경과** — 원장 건강 확인됨(aqr T0 06-05·combined 06-10 엔트리 정상, cron 매일 firing "9/21 영업일 skip"). 첫 실현 대조 ~2026-07-06(21영업일째). forward 검증은 설계대로 *대기 중*.
- **[HIGH] 오버핏 취약.** `out/aqr-pbo-validation.md` 자체 FRAGILE: PBO 0.390, effN 1.30/8(설정 거의 동일), 파일이 "실제 PBO 더 높을 가능성" 명시. → **부호 robust, +8%/yr 크기는 데이터마이닝 노출.**
- **[HIGH] 생존편향이 크기에 잔존.** `out/survivorship-audit.md`: 상폐 12/12 무료데이터 0 bar(엔진은 delisting_returns 지원하나 **데이터가 없음** — 샷건 PIT 시도와 동일 근본원인). CRSP 없이 절대 크기 정화 불가. 메가캡이라 방향 왜곡은 작음.
- **[HIGH] 엣지 US 전용.** `out/cross-market-replication.md`: US만 유의(IR CI [0.05,0.93]), Japan 미복제. 크기 일반화 근거 없음.

## 3. 배포 안전성 판정: 조건부 NO (명목 5% 초과 배분 금지)

엣지는 real-but-modest, 방향 robust, **크기 fragile**. 메가캡이라 전면폐기(compromised)는 아니므로 "소액 페이퍼/라이브 직전" 단계는 정당. **안전 자본배분 전제조건:**
1. `paper_oos` 원장에 **2~4분기 실현 forward 수익**이 백테스트 대비 합리 범위로 누적.
2. 목표를 **SPY-excess 기준**으로 명시(절대 +7.67% 아님).
3. 측정 파이프라인 독립 재현(샷건 교훈).

## 권장 조치
- **(P1) 라벨 정정**: 문서/메모리의 "+7.67%/yr **OOS**" → "walk-forward backtest (forward 실표본 0)". forward 증거는 paper_oos 성숙으로만 획득.
- **(P2) 상폐수익 데이터**: 엔진은 준비됐으니, 유료(CRSP/Norgate/Sharadar) 확보 시 크기의 생존편향 정화 가능. 그 전엔 크기를 SPY-excess로만.
- **(검증됨) 기계적 파이프라인은 신뢰 가능** — 샷건 버그 없음.
