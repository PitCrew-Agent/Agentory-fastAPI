"""MCP maintenance 서버 (BE_MCP05_MAINT01), 설비 정비·수리 이력 도구

실행: uv run mcp-maintenance (streamable-http, 포트 8103)
DB 조회 로직은 agentory.modules.admin.repository 재사용 (수리 이력 원천)
파라미터 검증·시간 파싱·직렬화는 이 계층에서 처리
"""

from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from agentory.core.db import SessionLocal
from agentory.modules.admin import repository

mcp = FastMCP("agentory-maintenance", host="0.0.0.0", port=8103)

MAX_REPAIR_LIMIT = 50  # 단일 조회 최대 수리 이력 행수
SUMMARY_WINDOW = 100  # 요약 집계 대상 최근 수리 행수


def _parse_time(value: str, field: str) -> datetime:
    # ISO 8601 문자열을 tz-aware datetime으로 파싱, 실패 시 ValueError
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} 시간 형식 오류(ISO 8601 필요): {value}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _serialize(repair: dict[str, Any]) -> dict[str, Any]:
    # repository dict의 datetime을 JSON 직렬화용 ISO 문자열로 변환
    row = dict(repair)
    repaired_at = row.get("repaired_at")
    if isinstance(repaired_at, datetime):
        row["repaired_at"] = repaired_at.isoformat()
    return row


@mcp.tool()
async def get_repair_history(
    equipment_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """특정 설비의 과거 수리 이력을 최근순 JSON 배열로 반환

    (BE_MCP05_MAINT01) 각 항목은 수리 시각·수리 직전 알람 코드·책임자·비고 포함
    start_time/end_time 미지정 시 전체 이력 대상, 수리 이력 없으면 빈 배열 반환
    """
    if not equipment_id:
        raise ValueError("equipment_id는 필수")
    limit = max(1, min(limit, MAX_REPAIR_LIMIT))
    start = _parse_time(start_time, "start_time") if start_time else None
    end = _parse_time(end_time, "end_time") if end_time else None
    async with SessionLocal() as session:
        rows = await repository.fetch_repairs_page(
            session, equipment_id=equipment_id, start=start, end=end, limit=limit
        )
    return [_serialize(r) for r in rows]


def _summarize(equipment_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    # 수리 이력 행 목록을 요약(수리 횟수·최근 수리 시각·직전 알람 코드별 재발 횟수)으로 집계
    # rows는 최근순 정렬 전제(fetch_repairs_page), DB 비의존 순수 함수라 단위 테스트 가능
    if not rows:
        return {
            "equipment_id": equipment_id,
            "repair_count": 0,
            "last_repaired_at": None,
            "alarm_code_counts": {},
        }
    counts: dict[str, int] = {}
    for row in rows:
        code = row.get("alarm_code_before")
        if code:
            counts[code] = counts.get(code, 0) + 1
    last_repaired_at = rows[0]["repaired_at"]
    return {
        "equipment_id": equipment_id,
        "repair_count": len(rows),  # 최근 window 내 수리 건수
        "last_repaired_at": last_repaired_at.isoformat()
        if isinstance(last_repaired_at, datetime)
        else last_repaired_at,
        "alarm_code_counts": counts,  # 수리 직전 알람 코드별 재발 횟수 (반복 고장 근거)
    }


@mcp.tool()
async def get_maintenance_summary(equipment_id: str) -> dict[str, Any]:
    """특정 설비의 정비 이력 요약을 반환

    (BE_MCP05_MAINT01) 최근 수리 이력을 집계해 수리 횟수·최근 수리 시각·수리 직전 알람 코드별
    재발 횟수를 산출, 이력 없으면 repair_count 0
    """
    if not equipment_id:
        raise ValueError("equipment_id는 필수")
    async with SessionLocal() as session:
        rows = await repository.fetch_repairs_page(
            session, equipment_id=equipment_id, limit=SUMMARY_WINDOW
        )
    return _summarize(equipment_id, rows)


def run() -> None:
    # MCP maintenance 서버를 streamable-http 트랜스포트로 기동
    mcp.run(transport="streamable-http")
