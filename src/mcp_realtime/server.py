"""MCP realtime 서버 (BE_MCP01_SERVER01), 실시간 원격 측정·설비 메타데이터 도구

실행: uv run mcp-realtime (streamable-http, 포트 8101)
DB 조회 로직은 agentory.modules.telemetry.repository 재사용
파라미터 검증·시간 파싱은 이 계층에서 처리
"""

from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from agentory.core.db import SessionLocal
from agentory.modules.telemetry import repository

mcp = FastMCP("agentory-realtime", host="0.0.0.0", port=8101)


def _parse_time(value: str, field: str) -> datetime:
    """ISO 8601 문자열을 tz-aware datetime으로 파싱, 실패 시 ValueError

    타임존 정보가 없으면 UTC로 간주 (timestamptz 컬럼 비교 안전성)
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} 시간 형식 오류(ISO 8601 필요): {value}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@mcp.tool()
async def get_sensor_logs(
    start_time: str,
    end_time: str,
    equipment_id: str | None = None,
    line_name: str | None = None,
) -> list[dict[str, Any]]:
    """특정 라인/설비의 지정 시간 내 센서값(온도·압력)·에러 로그를 JSON 배열로 반환

    (BE_MCP02_TELEMETRY01) equipment_id 또는 line_name 중 하나 필수
    결과 없음 시 빈 배열 반환
    """
    if not equipment_id and not line_name:
        raise ValueError("equipment_id 또는 line_name 중 하나는 필수")
    start = _parse_time(start_time, "start_time")
    end = _parse_time(end_time, "end_time")

    async with SessionLocal() as session:
        return await repository.fetch_sensor_logs(
            session,
            start_time=start,
            end_time=end,
            equipment_id=equipment_id,
            line_name=line_name,
        )


@mcp.tool()
async def get_alarm_history(
    equipment_id: str,
    start_time: str,
    end_time: str,
    alarm_code: str | None = None,
) -> list[dict[str, Any]]:
    """지정 기간 내 알람 코드별 발생 횟수·최초/최근 발생 시각 집계 반환

    (BE_MCP02_TELEMETRY02) 반환: [{alarm_code, count, first_seen, last_seen}]
    """
    start = _parse_time(start_time, "start_time")
    end = _parse_time(end_time, "end_time")

    async with SessionLocal() as session:
        return await repository.fetch_alarm_history(
            session,
            equipment_id=equipment_id,
            start_time=start,
            end_time=end,
            alarm_code=alarm_code,
        )


@mcp.tool()
async def get_equipment_metadata(
    equipment_id: str | None = None,
    line_name: str | None = None,
) -> list[dict[str, Any]]:
    """설비 설치 위치·담당 부서·공정 단계 메타데이터 조회

    (BE_MCP03_MASTER01) 존재하지 않는 설비면 빈 배열 반환
    """
    if not equipment_id and not line_name:
        raise ValueError("equipment_id 또는 line_name 중 하나는 필수")

    async with SessionLocal() as session:
        return await repository.fetch_equipment_metadata(
            session, equipment_id=equipment_id, line_name=line_name
        )


def run() -> None:
    mcp.run(transport="streamable-http")
