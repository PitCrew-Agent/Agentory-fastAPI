# MCP 실시간 조회 서버 (BE_MCP01_SERVER01)

이 문서는 설비의 실시간 센서·알람·메타데이터를 제공하는 MCP 서버의 역할과 도구를 설명합니다.

## MCP 개요

MCP(Model Context Protocol)는 AI 에이전트가 외부 도구·데이터를 호출하기 위한 표준 규격입니다.
서버가 도구를 정의해 노출하면, 클라이언트인 에이전트가 도구 목록을 조회하고 필요한 도구를
자율적으로 호출합니다. 본 과제는 데이터 소스를 MCP 표준 도구로 추상화하도록 요구합니다(REQ-T-02).

## 이 서버의 역할

시뮬레이터가 `equipment_telemetry`에 적재한 정형 데이터를 에이전트가 조회할 수 있도록 도구로
추상화합니다. 별도 프로세스로 실행되며 streamable-http 트랜스포트(포트 8101)로 통신합니다.

```
[AI 에이전트 (MCP 클라이언트)]
        │  list_tools / call_tool (streamable-http)
        ▼
[mcp-realtime 서버]  →  telemetry.repository  →  PostgreSQL
```

## 제공 도구

| 도구 | 기능 ID | 용도 |
| --- | --- | --- |
| `get_sensor_logs` | BE_MCP02_TELEMETRY01 | 기간별 센서값·에러 로그 조회 |
| `get_alarm_history` | BE_MCP02_TELEMETRY02 | 알람 코드별 발생 횟수·시각 집계 |
| `get_equipment_metadata` | BE_MCP03_MASTER01 | 설비 위치·부서·공정 단계 조회 |

### get_sensor_logs

특정 라인/설비의 지정 기간 센서 로그를 시간순으로 반환합니다.

- 입력: `start_time`, `end_time`(ISO 8601, 필수), `equipment_id` 또는 `line_name`(하나 필수)
- 출력: `[{equipment_id, timestamp, temperature, pressure, rf_power, gas_flow, alarm_code}]`
- `line_name`을 주면 해당 라인 소속 설비를 조인해 조회하며, 결과가 없으면 빈 배열을 반환합니다.

### get_alarm_history

지정 기간 내 알람 코드별 발생 횟수와 최초·최근 발생 시각을 집계합니다.

- 입력: `equipment_id`, `start_time`, `end_time`(필수), `alarm_code`(선택)
- 출력: `[{alarm_code, count, first_seen, last_seen}]`, 발생 횟수 내림차순
- 알람이 없는(NULL) 로그는 집계에서 제외합니다.

### get_equipment_metadata

설비의 설치 위치·담당 부서·공정 단계를 반환합니다.

- 입력: `equipment_id` 또는 `line_name`(하나 필수)
- 출력: `[{equipment_id, line_name, process_type, location, manager_dept}]`
- 존재하지 않는 설비면 빈 배열을 반환합니다.

## 조회 로직 공용화

세 도구의 실제 DB 조회는 `agentory.modules.telemetry.repository`에 구현되어 있습니다. MCP
서버는 파라미터 검증·시간 파싱만 담당하고 조회 로직을 재사용하므로, 디지털 트윈용 REST 등
다른 소비자도 동일한 로직을 사용할 수 있습니다.

## 파라미터 검증

- 시간 인자는 ISO 8601 문자열로 받으며, 타임존 정보가 없으면 UTC로 간주합니다.
- 시간 형식이 잘못되었거나 필수 식별자(`equipment_id`/`line_name`)가 모두 비면 오류를 반환합니다.

## 실행 방법

```bash
uv run mcp-realtime          # streamable-http, 포트 8101
docker compose up -d mcp-realtime
```

DB 스키마와 시드가 선행되어야 하며, 시뮬레이터가 데이터를 적재하고 있으면 실시간에 가까운
조회가 가능합니다.

## 관련 문서

- 데이터베이스 설계: [../database.md](../database.md)
- 센서 데이터 시뮬레이터: [../simulator.md](../simulator.md)
