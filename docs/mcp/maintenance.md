# MCP 정비 이력 서버 (BE_MCP05_MAINT01)

이 문서는 설비의 과거 수리·정비 이력을 제공하는 MCP 서버의 역할과 도구를 설명합니다.

## 이 서버의 역할

설비가 과거에 언제·무슨 알람으로·어떻게 수리됐는지는 실시간 센서(realtime)도 매뉴얼(knowledge)도
답하지 못하는 별개 진단 축입니다. 이 서버는 `equipment_repairs` 이력을 에이전트가 조회할 수 있도록
도구로 추상화합니다. 별도 프로세스로 실행되며 streamable-http 트랜스포트(포트 8103)로 통신합니다.

```
[AI 에이전트 (MCP 클라이언트)]
        │  list_tools / call_tool (streamable-http)
        ▼
[mcp-maintenance 서버]  →  admin.repository (fetch_repairs_page)  →  PostgreSQL
```

## 제공 도구

| 도구 | 기능 ID | 용도 |
| --- | --- | --- |
| `get_repair_history` | BE_MCP05_MAINT01 | 설비별 수리 이력(시각·직전 알람·책임자·비고) 최근순 조회 |
| `get_maintenance_summary` | BE_MCP05_MAINT01 | 수리 횟수·최근 수리 시각·직전 알람 코드별 재발 횟수 요약 |

### get_repair_history

특정 설비의 과거 수리 이력을 최근순으로 반환합니다.

- 입력: `equipment_id`(필수), `start_time`·`end_time`(선택, ISO 8601), `limit`(기본 20, 최대 50)
- 출력: `[{id, equipment_id, repaired_at, alarm_code_before, repaired_by_name, note}]`
- 기간 미지정 시 전체 이력 대상, 수리 이력이 없으면 빈 배열을 반환합니다.

### get_maintenance_summary

특정 설비의 정비 이력을 집계해 반복 고장 근거를 제공합니다.

- 입력: `equipment_id`(필수)
- 출력: `{equipment_id, repair_count, last_repaired_at, alarm_code_counts}`
- `alarm_code_counts`는 수리 직전 알람 코드별 재발 횟수로, 예를 들어 `{"ERR-401": 2}`는 동일 알람으로
  2회 수리된 반복 고장 정황을 뜻합니다. 이력이 없으면 `repair_count`는 0입니다.

## 조회 로직 공용화 · 닫힌 루프

수리 이력 조회는 `agentory.modules.admin.repository.fetch_repairs_page`를 재사용하고, MCP 서버는
파라미터 검증·시간 파싱·직렬화만 담당합니다. 수리 이력은 두 경로로 적재됩니다.

- 수리 API `POST /admin/equipment/{id}/repair`(ADR-0007)
- 작업 로그 완료 `POST /work-logs/{id}/complete`의 수리류 처리(NEW_LOOP01_WORKLOG01)

두 경로 모두 `create_repair`로 단일화되어 `alarm_code_before`를 자동 스냅샷하므로, 등록된 수리는
이 서버가 그대로 조회합니다. 즉 **수리 등록 → 정비 이력 조회**가 닫힌 루프를 이룹니다.

## 정량 근거

| 지표 | 기존 (2 워커) | 현재 (3 워커) |
| --- | --- | --- |
| 에이전트 도구 도메인 | 2 (센서·매뉴얼) | 3 (센서·매뉴얼·정비 이력) |
| 진단 답변 근거 축 | 현상 + 조치 | 현상 + 이력 + 조치 |
| 정비 이력 조회 도구 | 0 | 2 |

라이브 실측(실 gpt-5-mini)에서 "이 설비 전에도 고장났어?" 질의가 Supervisor→maintenance 라우팅→
`get_repair_history` 호출→실 DB 이력 회수→답변 반영으로 완주함을 확인했습니다.

## 실행 방법

```bash
uv run mcp-maintenance          # streamable-http, 포트 8103
docker compose up -d mcp-maintenance
```

라이브 데모 시 realtime·knowledge·maintenance 세 서버를 모두 기동해야 각 워커가 도구를 수급합니다.

## 관련 문서

- 에이전트 아키텍처: [../agent/architecture.md](../agent/architecture.md)
- 설비 수리 이력: [../adr/0007-equipment-repair.md](../adr/0007-equipment-repair.md)
- 데이터베이스 설계: [../database.md](../database.md)
