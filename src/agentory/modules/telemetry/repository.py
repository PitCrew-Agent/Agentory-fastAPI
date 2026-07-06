"""텔레메트리 조회 레포지토리

MCP realtime 서버와 트윈용 REST가 공유하는 조회 로직
JSON 직렬화 가능한 dict를 반환하므로 MCP 도구가 그대로 노출 가능
(BE_MCP02_TELEMETRY01 / BE_MCP02_TELEMETRY02 / BE_MCP03_MASTER01)
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry


def _num(value: Decimal | None) -> float | None:
    # Decimal을 JSON 친화적인 float로 변환, None은 유지
    return float(value) if value is not None else None


def _telemetry_to_dict(row: EquipmentTelemetry) -> dict[str, Any]:
    # 텔레메트리 행 하나를 JSON 직렬화용 dict로 변환
    return {
        "equipment_id": row.equipment_id,
        "timestamp": row.timestamp.isoformat(),
        "temperature": _num(row.temperature),
        "pressure": _num(row.pressure),
        "rf_power": _num(row.rf_power),
        "gas_flow": _num(row.gas_flow),
        "alarm_code": row.alarm_code,
    }


async def fetch_sensor_logs(
    session: AsyncSession,
    *,
    start_time: datetime,
    end_time: datetime,
    equipment_id: str | None = None,
    line_name: str | None = None,
) -> list[dict[str, Any]]:
    # 센서 로그 조회 (BE_MCP02_TELEMETRY01)
    # 지정 기간 필터 + 시간순 정렬
    stmt = (
        select(EquipmentTelemetry)
        .where(
            EquipmentTelemetry.timestamp >= start_time,
            EquipmentTelemetry.timestamp <= end_time,
        )
        .order_by(EquipmentTelemetry.timestamp)
    )
    if equipment_id:
        # 단일 설비로 좁힘
        stmt = stmt.where(EquipmentTelemetry.equipment_id == equipment_id)
    elif line_name:
        # 라인 소속 설비 id 서브쿼리로 필터
        line_equipment = select(EquipmentMaster.equipment_id).where(
            EquipmentMaster.line_name == line_name
        )
        stmt = stmt.where(EquipmentTelemetry.equipment_id.in_(line_equipment))

    rows = await session.scalars(stmt)
    # 결과 없으면 빈 목록 반환
    return [_telemetry_to_dict(r) for r in rows]


async def fetch_alarm_history(
    session: AsyncSession,
    *,
    equipment_id: str,
    start_time: datetime,
    end_time: datetime,
    alarm_code: str | None = None,
) -> list[dict[str, Any]]:
    # 알람 이력 집계 (BE_MCP02_TELEMETRY02)
    # 알람 코드별 발생 횟수·최초/최근 시각 집계, 다발 순 정렬 (NULL 알람 제외)
    stmt = (
        select(
            EquipmentTelemetry.alarm_code,
            func.count().label("count"),
            func.min(EquipmentTelemetry.timestamp).label("first_seen"),
            func.max(EquipmentTelemetry.timestamp).label("last_seen"),
        )
        .where(
            EquipmentTelemetry.equipment_id == equipment_id,
            EquipmentTelemetry.alarm_code.is_not(None),
            EquipmentTelemetry.timestamp >= start_time,
            EquipmentTelemetry.timestamp <= end_time,
        )
        .group_by(EquipmentTelemetry.alarm_code)
        .order_by(func.count().desc())
    )
    if alarm_code:
        # 특정 알람 코드로 좁힘
        stmt = stmt.where(EquipmentTelemetry.alarm_code == alarm_code)

    rows = await session.execute(stmt)
    # 코드별 집계 행을 dict로 변환
    return [
        {
            "alarm_code": code,
            "count": count,
            "first_seen": first_seen.isoformat(),
            "last_seen": last_seen.isoformat(),
        }
        for code, count, first_seen, last_seen in rows
    ]


async def fetch_equipment_metadata(
    session: AsyncSession,
    *,
    equipment_id: str | None = None,
    line_name: str | None = None,
) -> list[dict[str, Any]]:
    # 설비 메타데이터 조회 (BE_MCP03_MASTER01)
    stmt = select(EquipmentMaster)
    if equipment_id:
        # 특정 설비 한 건
        stmt = stmt.where(EquipmentMaster.equipment_id == equipment_id)
    elif line_name:
        # 특정 라인 소속 전체
        stmt = stmt.where(EquipmentMaster.line_name == line_name)

    rows = await session.scalars(stmt)
    # 존재하지 않으면 빈 목록 반환
    return [
        {
            "equipment_id": e.equipment_id,
            "line_name": e.line_name,
            "process_type": e.process_type,
            "location": e.location,
            "manager_dept": e.manager_dept,
            "manager_name": e.manager_name,
            "last_inspection_at": (
                e.last_inspection_at.isoformat() if e.last_inspection_at else None
            ),
        }
        for e in rows
    ]
