# ADR-0006: 장비별 알람 이력 조회 설계 (데이터 소스·조회 형태·인덱스)

- 상태: 승인 (2026-07-13)
- 결정자: 주희정
- 관련: [ADR-0001](0001-architecture.md), [ADR-0004](0004-realtime-sse.md), [docs/bench/alarm-query-index.md](../bench/alarm-query-index.md), [docs/database.md](../database.md), feature/91-alarm-history(#91), feature/121-per-variable-alarms(#122)

## 배경

디지털 트윈 상세 패널에서 설비를 선택했을 때 그 설비의 알람(에러) 이력을 조회하는 기능을
추가합니다(NEW_ALARM01_HISTORY01/02). 기존에도 알람을 다루는 경로가 있으나 목적이 다릅니다.

- 알림(notifications, [ADR-0004](0004-realtime-sse.md)): 상단 벨용 전역 피드입니다. 동일 설비+변수+알람은
  30분 버킷당 1건만 적재해 반복 알람 중복을 억제하며, 설비 단위 필터를 제공하지 않습니다.
- 원본 텔레메트리(equipment_telemetries): 설비별 tick마다 센서값과 `alarm_code`를 적재하는 원본
  로그입니다. 알람 이력의 최소 해상도 원천입니다.

본 ADR은 이 기능을 구현하며 내린 세 가지 결정(조회 형태·데이터 소스·조회 인덱스)을 기록합니다.

## 결정

### 1. 조회 형태: 타임라인 + 집계 요약 두 형태 제공

알람 이력을 두 엔드포인트로 제공합니다. 개별 발생 흐름을 보는 관점과 코드별 경향을 보는 관점이
현장에서 모두 필요하기 때문입니다.

- `GET /telemetry/equipment/{id}/alarms`: 발생 이벤트 타임라인, 발생 역순 커서 페이지네이션,
  기간·알람 코드 필터
- `GET /telemetry/equipment/{id}/alarms/summary`: 알람 코드별 발생 횟수·최초/최근 시각 집계

### 2. 데이터 소스: 타임라인은 원본 텔레메트리, 요약은 기존 집계 재사용

타임라인은 원본 텔레메트리에서 `alarm_code`가 있는 tick을 그대로 조회합니다. 이력은 전체 해상도가
필요한데, 알림 테이블은 30분 버킷 중복 억제로 같은 알람의 반복이 30분당 1건으로 뭉개져 실제 발생
흐름을 잃기 때문입니다. 집계 요약은 이미 MCP 도구가 쓰는 `fetch_alarm_history`(BE_MCP02_TELEMETRY02)를
재사용하고, REST 조회 편의를 위해 기간 인자만 선택값으로 완화했습니다(미지정 시 전체 이력).

심각도는 기존 `assess_status`(ERR→위험, WRN→주의)를 재사용합니다. 알림 메시지 문자열은 포함하지
않습니다. 설비가 이미 선택된 화면 맥락이라 코드·심각도만으로 충분하고, notification 모듈로의 불필요한
결합을 피하기 위함입니다.

### 3. 페이지네이션: (timestamp, log_id) 키셋 커서

발생 역순 `(timestamp, log_id)` 키셋 커서로 페이지네이션합니다. 새 알람이 위에 쌓여도 경계가 밀리지
않으며, 알림 목록([ADR-0004](0004-realtime-sse.md))의 커서 방식과 동형이라 프론트 재사용이 쉽습니다.

### 4. 조회 인덱스: 부분 인덱스 채택

`(equipment_id, timestamp) WHERE alarm_code IS NOT NULL` 부분 인덱스
(`ix_telemetry_equip_alarm_time`, 마이그레이션 0014)를 추가합니다. 알람 행이 전체의 1% 미만으로
희소한 특성을 살려, 알람 행만 담는 작은 인덱스로 타임라인·코드 필터·집계를 모두 커버합니다.

### 5. per-variable 알람 저널 확장 (feature/121-per-variable-alarms #122)

위 1~4는 tick당 `alarm_code` 1개(대표 코드) 모델을 전제합니다. 그러나 한 설비가 온도 급성과 압력
드리프트를 동시에 갖는 실제 상황을 담지 못하고, 복합 코드(ERR-402=온도+압력, ERR-901=RF+온도)가 한
코드로 두 변수에 걸쳐 도넛 차트의 센서별 구분을 흐렸습니다. 실무 알람 모델(ISA-18.2)대로 값과 알람을
분리한 **센서 변수별 알람 저널** `equipment_alarms`를 신설했습니다(상세 스키마는 [database.md](../database.md)).

- `equipment_alarms`: `equipment_id`·`metric`(temperature/pressure/rf_power/gas_flow)·`alarm_code`·
  `severity`·`raised_at`·`cleared_at`(NULL=활성), 발생~해제 생명주기를 변수별로 기록
- 도넛 센서별 집계 `GET /telemetry/equipment/{id}/alarms/sensors`: `metric` 기준 `GROUP BY` 집계
- 기존 `equipment_telemetries.alarm_code`는 **활성 알람 worst-of 대표값으로 파생 유지**해 incident·
  notification·worklog·SSE 계약을 무변경으로 보존
- 복합 코드(ERR-402·ERR-901)는 변수 간 코드 전이를 유발하므로 은퇴, 이상은 항상 단일변수 코드로 발생

| 지표 | 기존 | 현재 |
| --- | --- | --- |
| 설비당 동시 표현 알람 | 1 (tick당 코드 1개) | 4 (센서 변수 독립) |
| 도넛 집계 단위 | alarm_code | metric(센서) |
| 변수 간 코드 전이(복합코드) | 존재 (ERR-402·901) | 0 (단일변수 코드) |
| 하위 소비 모듈·SSE 계약 변경 | 해당 없음 | 0건 (대표값 파생) |

## 정량 평가 (실측)

인덱스 구성 4종을 별도 벤치 테이블(2,000,000행, 알람 0.6%)에서 비교했습니다. 측정 하네스는
`scripts/bench/alarm_query_index_bench.py`, 원자료는 `scripts/bench/alarm_query_index_result.json`,
상세 방법·전체 표는 [docs/bench/alarm-query-index.md](../bench/alarm-query-index.md)에 있습니다.

| 쿼리 (클라이언트 중앙값) | 인덱스 없음 | (equipment_id, timestamp) | 부분 인덱스 | (equipment_id, alarm_code, timestamp) |
| --- | --- | --- | --- | --- |
| 타임라인 첫 페이지 | 39.48ms | 0.47ms | 0.36ms | 0.56ms |
| 알람 코드 필터 | 39.08ms | 40.71ms | 0.47ms | 0.32ms |
| 집계 요약 | 39.20ms | 40.42ms | 0.48ms | 0.48ms |
| 인덱스 크기 | 0 | 61.6MB | 0.38MB | 79.3MB |

### 핵심 발견

- 현 운영 인덱스 `(equipment_id, timestamp)`는 타임라인 첫 페이지에는 효과가 크지만, `alarm_code`
  조건이 인덱스에 없어 **코드 필터·집계 요약에서 해당 설비 전체 행을 훑어 약 40ms로 순차 스캔과 동일
  수준으로 저하**됩니다.
- 부분 인덱스는 알람 행만 담아 **4개 조회 패턴 전부 1ms 미만**을 달성하며, 크기도 0.38MB로 운영
  인덱스의 1/160입니다. 코드 필터도 인덱스가 이미 알람 행만 담고 있어 소량 비트맵 스캔으로 빠릅니다.
- 전체 복합 인덱스 `(equipment_id, alarm_code, timestamp)`는 코드 필터 단건만 근소하게 빠를 뿐,
  모든 행을 인덱싱해 79.3MB로 가장 크고 쓰기 비용도 큽니다.
- 운영 시드(190,800행)에서 부분 인덱스는 56KB이며(전체 인덱스 6MB의 1/110), 실데이터 코드 필터·
  집계 조회가 이 인덱스를 타 약 0.18ms에 실행됨을 EXPLAIN ANALYZE로 확인했습니다.

## 대안 검토

| 대안 | 기각 사유 |
| --- | --- |
| 알림(notifications) 테이블 재사용 | 30분 버킷 중복 억제로 반복 알람이 뭉개져 발생 흐름을 잃음, 이력 원천으로 부적합 |
| 인덱스 없이 순차 스캔 | 조회당 약 40ms, 데이터 누적 시 선형 악화 |
| `(equipment_id, timestamp)` 인덱스 재사용 | 코드 필터·집계에서 약 40ms로 저하, 알람 조회를 못 살림 |
| `(equipment_id, alarm_code, timestamp)` 전체 복합 인덱스 | 조회는 빠르나 79.3MB로 최대, 쓰기 비용 큼, 이득 대비 과함 |
| 응답에 알림 메시지 문자열 포함 | 설비 선택 맥락에선 코드·심각도로 충분, notification 모듈 결합만 추가 |

## 결과 / 미해결

- (1~4) 신규 테이블·컬럼 없이 기존 원본 텔레메트리 위에 부분 인덱스 하나만 추가해 조회를 성립시켰습니다.
  이후 §5(#122)에서 센서 변수별 알람 저널 `equipment_alarms`를 신설해 per-variable 모델로 확장했습니다.
- 벤치는 별도 테이블에서 수행 후 제거하므로 운영 시드·autogenerate 드리프트에 영향이 없습니다.
- 남은 리스크로 기록합니다. 텔레메트리 데이터가 장기 누적되면 요약 집계 비용이 다시 커질 수 있으며,
  이 경우 기간 필수화 또는 코드별 사전 집계 테이블을 후속 과제로 검토합니다.
