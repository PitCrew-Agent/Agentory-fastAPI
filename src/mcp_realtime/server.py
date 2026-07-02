"""MCP realtime 서버 (BE_MCP01_SERVER01), 실시간 원격 측정·설비 메타데이터 도구

실행: uv run mcp-realtime (streamable-http, 포트 8101)
DB 조회 로직은 agentory.modules.telemetry.repository 재사용
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agentory-realtime", host="0.0.0.0", port=8101)


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
    # TODO(주희정): telemetry.repository 연동
    raise NotImplementedError


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
    # TODO(주희정): telemetry.repository 연동
    raise NotImplementedError


@mcp.tool()
async def get_equipment_metadata(
    equipment_id: str | None = None,
    line_name: str | None = None,
) -> list[dict[str, Any]]:
    """설비 설치 위치·담당 부서·공정 단계 메타데이터 조회

    (BE_MCP03_MASTER01) 존재하지 않는 설비 ID 시 빈 배열 + 안내 메시지
    """
    # TODO(주희정): telemetry.repository 연동
    raise NotImplementedError


def run() -> None:
    mcp.run(transport="streamable-http")
