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
    return float(value) if value is not None else None


def _telemetry_to_dict(row: EquipmentTelemetry) -> dict[str, Any]:
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
    """지정 기간 내 센서 로그를 시간순 조회 (BE_MCP02_TELEMETRY01)

    equipment_id 우선, 없으면 line_name으로 소속 설비를 조인해 조회
    """
    stmt = (
        select(EquipmentTelemetry)
        .where(
            EquipmentTelemetry.timestamp >= start_time,
            EquipmentTelemetry.timestamp <= end_time,
        )
        .order_by(EquipmentTelemetry.timestamp)
    )
    if equipment_id:
        stmt = stmt.where(EquipmentTelemetry.equipment_id == equipment_id)
    elif line_name:
        line_equipment = select(EquipmentMaster.equipment_id).where(
            EquipmentMaster.line_name == line_name
        )
        stmt = stmt.where(EquipmentTelemetry.equipment_id.in_(line_equipment))

    rows = await session.scalars(stmt)
    return [_telemetry_to_dict(r) for r in rows]


async def fetch_alarm_history(
    session: AsyncSession,
    *,
    equipment_id: str,
    start_time: datetime,
    end_time: datetime,
    alarm_code: str | None = None,
) -> list[dict[str, Any]]:
    """지정 기간 내 알람 코드별 발생 횟수·최초/최근 시각 집계 (BE_MCP02_TELEMETRY02)"""
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
        stmt = stmt.where(EquipmentTelemetry.alarm_code == alarm_code)

    rows = await session.execute(stmt)
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
    """설비 메타데이터 조회 (BE_MCP03_MASTER01)"""
    stmt = select(EquipmentMaster)
    if equipment_id:
        stmt = stmt.where(EquipmentMaster.equipment_id == equipment_id)
    elif line_name:
        stmt = stmt.where(EquipmentMaster.line_name == line_name)

    rows = await session.scalars(stmt)
    return [
        {
            "equipment_id": e.equipment_id,
            "line_name": e.line_name,
            "process_type": e.process_type,
            "location": e.location,
            "manager_dept": e.manager_dept,
        }
        for e in rows
    ]
