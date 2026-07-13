"""장애 대응 계획 컨텍스트 조회 (NEW_INCIDENT01_PLAN01)"""

from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.notification.models import Notification
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _telemetry_to_dict(row: EquipmentTelemetry | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "log_id": row.log_id,
        "equipment_id": row.equipment_id,
        "timestamp": row.timestamp,
        "temperature": _number(row.temperature),
        "pressure": _number(row.pressure),
        "rf_power": _number(row.rf_power),
        "gas_flow": _number(row.gas_flow),
        "alarm_code": row.alarm_code,
    }


async def fetch_incident_context(
    session: AsyncSession,
    notification_id: int,
) -> dict[str, Any] | None:
    notification = await session.get(Notification, notification_id)
    if notification is None:
        return None

    equipment = await session.get(EquipmentMaster, notification.equipment_id)
    incident_row = None
    if notification.source_log_id is not None:
        source_row = await session.get(EquipmentTelemetry, notification.source_log_id)
        if source_row is not None and source_row.equipment_id == notification.equipment_id:
            incident_row = source_row

    if incident_row is None:
        window = timedelta(minutes=5)
        nearby_stmt = (
            select(EquipmentTelemetry)
            .where(
                EquipmentTelemetry.equipment_id == notification.equipment_id,
                EquipmentTelemetry.timestamp >= notification.occurred_at - window,
                EquipmentTelemetry.timestamp <= notification.occurred_at + window,
            )
            .order_by(EquipmentTelemetry.timestamp.asc())
        )
        nearby_rows = list(await session.scalars(nearby_stmt))
        if nearby_rows:
            incident_row = min(
                nearby_rows,
                key=lambda row: abs((row.timestamp - notification.occurred_at).total_seconds()),
            )

    baseline_stmt = (
        select(EquipmentTelemetry)
        .where(
            EquipmentTelemetry.equipment_id == notification.equipment_id,
            EquipmentTelemetry.timestamp < notification.occurred_at,
            EquipmentTelemetry.timestamp >= notification.occurred_at - timedelta(minutes=30),
            EquipmentTelemetry.alarm_code.is_(None),
        )
        .order_by(EquipmentTelemetry.timestamp.desc())
        .limit(20)
    )
    baseline_rows = list(await session.scalars(baseline_stmt))

    latest_stmt = (
        select(EquipmentTelemetry)
        .where(EquipmentTelemetry.equipment_id == notification.equipment_id)
        .order_by(EquipmentTelemetry.timestamp.desc())
        .limit(1)
    )
    latest_row = await session.scalar(latest_stmt)

    return {
        "notification": {
            "id": notification.id,
            "occurred_at": notification.occurred_at,
            "equipment_id": notification.equipment_id,
            "alarm_code": notification.alarm_code,
            "message": notification.message,
        },
        "equipment": {
            "process_type": equipment.process_type if equipment else None,
            "line_name": equipment.line_name if equipment else None,
            "location": equipment.location if equipment else None,
        },
        "incident": _telemetry_to_dict(incident_row),
        "baseline": [_telemetry_to_dict(row) for row in baseline_rows],
        "latest": _telemetry_to_dict(latest_row),
    }
