# ADR-0007: 설비 수리 이력·시뮬레이터 힐 윈도우 동기화

- 상태: 승인 (2026-07-13)
- 결정자: 주희정
- 관련: [ADR-0001](0001-architecture.md), [ADR-0004](0004-realtime-sse.md), [ADR-0005](0005-simulator-spc.md), [ADR-0006](0006-alarm-history-query.md), [docs/simulator.md](../simulator.md)

## 배경

실장비와 연결하지 않는 시뮬레이션 환경에서, 플랫폼상 설비를 "수리"한다고 가정하고 작업 현황을
조회하는 흐름을 만듭니다. 수리하면 두 가지가 일어나야 합니다.

- 수리 이력 적재: 누가·언제·어떤 설비를 수리했는지 남겨 작업 현황 조회와 재정비 에이전트(타 팀)의
  입력이 됩니다.
- 시뮬레이터 정상화: 수리한 설비는 이후 텔레메트리가 정상 범주로 생성되어야 합니다.

문제는 시뮬레이터가 API와 별도 프로세스(엔트리포인트)라는 점입니다. 수리 API(agentory-api)가
"이 설비 수리됨" 신호를 시뮬레이터에 전달할 채널이 필요합니다. 재정비 에이전트는 본 작업 범위 밖이며,
`equipment_repairs` 이력 테이블이 그 팀과의 계약 지점이 됩니다.

## 결정

### 1. 신호 채널: DB 래치 (equipment_masters.repaired_at)

시뮬레이터는 이미 매 tick `equipment_masters`를 조회하므로 DB가 자연스러운 채널입니다. 수리 시
`equipment_masters.repaired_at`을 현재 시각으로 래치하고, 시뮬레이터가 이 값을 읽어 정상 생성을
결정합니다. Redis 플래그(휘발·시뮬레이터 결합 추가)나 이력 테이블 파생(매 tick 집계, 복잡)보다
단순하고 재시작에도 유지됩니다.

### 2. 재고장은 힐 윈도우로 지연 (기본 60분)

시연 시간이 3분이라 데모 중에는 재고장이 보이면 안 되지만, 개발용으로 시뮬레이터를 장시간 돌릴 때는
고장 데이터가 계속 흘러야 합니다. 두 요구를 힐 윈도우로 동시에 만족합니다.

- 수리 후 `sim_repair_heal_minutes`(기본 60) 동안 해당 설비는 시나리오와 무관하게 정상 강제
- 윈도우 경과 후에는 원래 시나리오(preset/target 고장)가 자연 재개되어 재고장

즉 재고장은 "일어나더라도 최소 1시간 이후"가 보장됩니다. 영구 래치(수리=영원히 정상)는 재정비
에이전트가 고장→수리→재검증 루프를 한 번밖에 못 돌려 기각했고, `sim_scenario`를 DB로 승격하는 방식은
시나리오 소스를 CLI에서 DB로 옮기는 큰 변경이라 현 단계에서는 과합니다.

시뮬레이터 결정 로직은 순수 함수 `_select_spec`로 분리해 테스트 가능하게 했습니다.

```python
if repaired_at is not None and now - repaired_at < heal_window:
    return NORMAL          # 힐 윈도우 내 정상 강제
# 이하 기존 preset/target 로직 (윈도우 경과 시 고장 재개)
```

### 3. 이력 테이블·수리 API (admin)

- `equipment_repairs`: `equipment_id`·`repaired_by`(users FK, SET NULL)·`repaired_at`·
  `alarm_code_before`(수리 직전 알람 스냅샷)·`note`, 인덱스 `(equipment_id, repaired_at)`
- `POST /admin/equipment/{id}/repair`: 수리 처리, 이력 INSERT + 마스터 `repaired_at`·
  `last_inspection_at`·`alarm_cleared_at`를 원자적으로 갱신
- `GET /admin/repairs`: 작업 현황(설비·책임자·기간 필터, 커서 페이지네이션)
- `GET /admin/equipment/{id}/repairs`: 설비별 수리 이력

수리 권한은 관리자(admin)와 현장 책임자(field_engineer)를 모두 허용하고, 현재는 전체 설비를 대상으로
합니다. `repaired_by`는 로그인 유저로 자동 기록합니다.

## 결과 / 미해결

- 수리 시 `alarm_cleared_at`도 함께 갱신해 기존 알람 래치([ADR-0004])를 해제하며, 상태 판정은 최신
  tick 기준이라 정상 tick이 들어오면 3D 뷰가 자동으로 양호로 전환됩니다.
- 남은 리스크로 기록합니다. field_engineer의 담당 설비 범위 제한은 현재 미적용(전체 허용)이며, 필요 시
  책임자·담당 라인 기준으로 좁힐 수 있습니다. 힐 윈도우 경과 후 드리프트 시나리오는 전역 tick 기준이라
  재개 시 즉시 강한 고장으로 나타날 수 있습니다.
