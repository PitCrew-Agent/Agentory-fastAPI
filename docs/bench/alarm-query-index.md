# 장비별 알람 이력 조회 인덱스 성능 벤치 (NEW_ALARM01_HISTORY01/02)

설계 결정 배경과 대안 검토는 [ADR-0006](../adr/0006-alarm-history-query.md)에 있으며,
본 문서는 그 결정을 뒷받침한 측정 방법과 원자료를 정리합니다.

## 목적

장비별 알람 이력 조회 기능(타임라인·집계 요약)이 원본 텔레메트리 테이블
(`equipment_telemetries`)을 어떤 인덱스로 조회해야 가장 빠른지 정량으로 확인합니다.
알람 행은 전체 텔레메트리의 1% 미만으로 희소하므로, 일반 복합 인덱스보다 부분 인덱스가
유리할 것으로 보고 인덱스 구성 4종을 실측 비교했습니다.

## 측정 환경·방법

운영 시드 테이블을 건드리지 않도록 별도 벤치 테이블(`bench_alarm_telemetry`)을
스케일업 적재한 뒤 측정하고, 측정 종료 시 벤치 테이블을 제거합니다.

| 항목 | 값 |
| --- | --- |
| DB | PostgreSQL 16 (pgvector/pgvector:pg16), 로컬 docker |
| 총 행수 | 2,000,000 |
| 알람 행수 | 11,978 (약 0.6%) |
| 설비 수 | 20 |
| 대상 설비 알람 행수 | 579 |
| 반복 횟수 | 쿼리당 클라이언트 왕복 50회 (워밍업 3회 선행) |
| 지표 | 플래너 실행시간(EXPLAIN ANALYZE) + 클라이언트 중앙값·p95 |

측정 스크립트는 `scripts/bench/alarm_query_index_bench.py`, 원자료는
`scripts/bench/alarm_query_index_result.json`입니다.

### 워크로드 (엔드포인트 SQL과 동형 4종)

| 쿼리 | 설명 |
| --- | --- |
| timeline_first | 알람 타임라인 첫 페이지 (`equipment_id` + `alarm_code IS NOT NULL`, 발생 역순 LIMIT) |
| timeline_range | 타임라인 + 기간 필터 (`timestamp` 범위) |
| timeline_code | 타임라인 + 알람 코드 필터 (`alarm_code = ?`) |
| summary_aggregate | 코드별 집계 요약 (`GROUP BY alarm_code`) |

### 인덱스 구성 (4종)

| 구성 | 정의 |
| --- | --- |
| none | 보조 인덱스 없음 (PK만), 순차 스캔 기준선 |
| equip_time | `(equipment_id, timestamp)`, 현 운영 인덱스 |
| equip_time_partial | `(equipment_id, timestamp) WHERE alarm_code IS NOT NULL`, 알람 전용 부분 인덱스 |
| equip_code_time | `(equipment_id, alarm_code, timestamp)`, 코드까지 포함한 전체 복합 인덱스 |

## 결과 (클라이언트 중앙값, ms)

| 쿼리 | none | equip_time | equip_time_partial | equip_code_time |
| --- | --- | --- | --- | --- |
| timeline_first | 39.48 | 0.47 | **0.36** | 0.56 |
| timeline_range | 39.12 | 2.48 | **0.34** | 0.38 |
| timeline_code | 39.08 | 40.71 | **0.47** | 0.32 |
| summary_aggregate | 39.20 | 40.42 | **0.48** | 0.48 |

인덱스 크기 (2M행 기준)

| 구성 | 인덱스 크기 | 상대 크기 |
| --- | --- | --- |
| equip_time | 61.6 MB | 기준 |
| equip_time_partial | 0.38 MB | 1/160 |
| equip_code_time | 79.3 MB | 1.29배 |

스캔 방식 (EXPLAIN)

| 쿼리 | equip_time | equip_time_partial | equip_code_time |
| --- | --- | --- | --- |
| timeline_first | Index Scan | Index Scan | Bitmap Heap Scan |
| timeline_range | Index Scan | Index Scan | Bitmap Heap Scan |
| timeline_code | Bitmap Heap Scan | Bitmap Heap Scan | Index Scan |
| summary_aggregate | Bitmap Heap Scan | Bitmap Heap Scan | Bitmap Heap Scan |

## 평가

- 인덱스가 없으면 4개 쿼리 모두 순차 스캔으로 약 39ms이며, 행수에 선형 비례해 증가합니다
  (190k행 기준 약 4ms로 추정, 데이터 누적 시 계속 악화됩니다).
- 현 운영 인덱스(`equip_time`)는 타임라인 첫 페이지·기간 조회에는 효과가 크지만,
  **알람 코드 필터와 집계 요약에서는 여전히 약 40ms로 순차 스캔과 동일 수준**입니다.
  `alarm_code` 조건이 인덱스에 없어 해당 설비의 전체 행(약 10만 건)을 훑기 때문입니다.
- 부분 인덱스(`equip_time_partial`)는 알람 있는 행(0.6%)만 담아 **4개 쿼리 전부 1ms 미만**을
  달성하며, 크기도 0.38MB로 운영 인덱스의 1/160에 불과합니다. 알람 코드 필터는 인덱스가
  이미 알람 행만 담고 있어 소량 비트맵 스캔으로 충분히 빠릅니다.
- 전체 복합 인덱스(`equip_code_time`)도 조회는 빠르지만, 모든 행을 인덱싱해 79.3MB로
  가장 크고 쓰기 비용도 큽니다. 코드 필터 단일 쿼리에서만 부분 인덱스보다 근소하게 빠릅니다.

## 결론·권고

부분 인덱스 `equip_time_partial`을 채택합니다. 4개 조회 패턴 전부에서 sub-ms를 내면서
저장·쓰기 비용이 가장 작아, 조회 성능과 유지 비용의 균형이 가장 좋습니다.

마이그레이션 `0014_alarm_history_index`로 다음 인덱스를 추가했습니다.

```sql
CREATE INDEX ix_telemetry_equip_alarm_time
    ON equipment_telemetries (equipment_id, timestamp)
    WHERE alarm_code IS NOT NULL;
```

운영 시드(190k행)에서 이 인덱스는 56KB이며(전체 인덱스 6MB의 1/110), 실데이터 코드 필터·
집계 조회가 해당 인덱스를 타 약 0.18ms에 실행됨을 확인했습니다.

## 재현

```bash
docker compose up -d db
uv run python scripts/bench/alarm_query_index_bench.py --rows 2000000 --reps 50
```
