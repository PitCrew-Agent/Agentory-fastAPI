# 작업 로그 (NEW_LOOP01_WORKLOG01)

이 문서는 대응 작업 로그의 계획/완료 구조와, 완료 시 작업 유형에 따라 도메인 이력으로 적재하는
동작을 설명합니다. 체크리스트의 "점검 결과를 작업 로그에 등록" 흐름과 연계됩니다.

## 계획 → 완료 2단계 구조 (feature/132 #133)

기존에는 작업 로그가 '작업 내용' 단일 항목이었습니다. 계획과 실제 수행 결과를 구분해 기록하도록
'작업 계획(plan)'과 '작업 완료(completion)' 2단계로 구조화했습니다.

| 항목 | 기존 | 현재 |
| --- | --- | --- |
| 작업 로그 단계 | 1 (단일 '작업 내용') | 2 ('작업 계획' + '작업 완료') |
| 완료 시각 | 수동 입력(ended_at) | 완료 제출 시 서버 시각 자동(completed_at) |
| 수리 완료 → 수리 이력 | 수동 별도 등록 | 완료 시 자동 적재 |

- `plan`: 작성 시 필수, 무엇을 할지의 계획
- `completion`·`completed_at`: '작업 완료' 제출 시 채워지며, 완료 시각은 서버 `now`로 기록
- 담당자는 별도 필드 없이 작성자(worker_name, 로그인 유저)를 그대로 사용

## 완료 처리와 유형별 도메인 적재

`POST /work-logs/{id}/complete`(작성자만)는 완료 내용·완료 시각을 기록하고, 설비가 연결된 로그에
한해 작업 유형에 따라 도메인 이력에 적재합니다.

| 작업 유형 | 적재 대상 | 방식 |
| --- | --- | --- |
| 긴급수리·수리점검 (수리류) | `equipment_repairs` | `create_repair` 재사용, `repaired_by`=완료자, `alarm_code_before` 자동 스냅샷 |
| 정기점검·예방점검 (점검류) | `equipment_masters.last_inspection_at` | 완료 시각(date)으로 최신 점검일 갱신 |
| 기타 · 설비 미연결 | 없음 | 완료 시각만 기록 |

점검 이력은 별도 테이블 없이 작업 로그(점검류) 자체로 대체하고, 최신 점검일만 마스터에 갱신합니다.
수리는 이미 `equipment_repairs` 이력 테이블이 있어 그대로 적재합니다.

## 닫힌 루프: 수리 완료 → 정비 이력 → 진단

수리류 작업 완료가 `equipment_repairs`에 적재되면, maintenance 워커(BE_MCP05_MAINT01)가 이 이력을
조회해 챗봇 진단에 반영합니다. 즉 **작업 로그 완료 → 수리 이력 → 정비 이력 조회**가 하나의 루프를
이룹니다([docs/mcp/maintenance.md](mcp/maintenance.md), [ADR-0007](adr/0007-equipment-repair.md) §4).

## 모듈 경계

작업 로그 완료는 `equipment_repairs`(admin)·`equipment_masters`(telemetry)에 쓰는 크로스 모듈 동작이며,
수리 적재는 admin의 `create_repair`로 단일화해 수리 API와 동작을 일치시킵니다.

## 관련 문서

- 설비 수리 이력: [adr/0007-equipment-repair.md](adr/0007-equipment-repair.md)
- 정비 이력 MCP: [mcp/maintenance.md](mcp/maintenance.md)
